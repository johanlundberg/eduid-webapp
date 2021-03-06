# -*- coding: utf-8 -*-

from __future__ import absolute_import

from flask import Blueprint, current_app

from eduid_common.api.decorators import require_user, can_verify_identity, MarshalWith, UnmarshalWith
from eduid_common.api.helpers import add_nin_to_user, verify_nin_for_user
from eduid_common.api.exceptions import AmTaskFailed, MsgTaskFailed
from eduid_userdb.logs import LetterProofing
from eduid_webapp.letter_proofing import pdf
from eduid_webapp.letter_proofing import schemas
from eduid_webapp.letter_proofing.ekopost import EkopostException
from eduid_webapp.letter_proofing.helpers import create_proofing_state, check_state, get_address, send_letter

__author__ = 'lundberg'

letter_proofing_views = Blueprint('letter_proofing', __name__, url_prefix='', template_folder='templates')


@letter_proofing_views.route('/proofing', methods=['GET'])
@MarshalWith(schemas.LetterProofingResponseSchema)
@require_user
def get_state(user):
    current_app.logger.info('Getting proofing state for user {}'.format(user))
    proofing_state = current_app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)

    if proofing_state:
        current_app.logger.info('Found proofing state for user {}'.format(user))
        return check_state(proofing_state)
    return {'message': 'letter.no_state_found'}


@letter_proofing_views.route('/proofing', methods=['POST'])
@UnmarshalWith(schemas.LetterProofingRequestSchema)
@MarshalWith(schemas.LetterProofingResponseSchema)
@can_verify_identity
@require_user
def proofing(user, nin):
    current_app.logger.info('Send letter for user {} initiated'.format(user))
    proofing_state = current_app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)

    # No existing proofing state was found, create a new one
    if not proofing_state:
        # Create a LetterNinProofingUser in proofingdb
        proofing_state = create_proofing_state(user.eppn, nin)
        current_app.logger.info('Created proofing state for user {}'.format(user))

    # Add the nin used to initiate the proofing state to the user
    # NOOP if the user already have the nin
    add_nin_to_user(user, proofing_state)

    if proofing_state.proofing_letter.is_sent:
        current_app.logger.info('A letter has already been sent to the user. ')
        current_app.logger.debug('Proofing state: {}'.format(proofing_state.to_dict()))
        result = check_state(proofing_state)
        if not result['letter_expired']:
            return result
        else:
            # XXX Are we sure that the user wants to send a new letter?
            current_app.logger.info('The letter has expired. Sending a new one...')
    try:
        address = get_address(user, proofing_state)
        if not address:
            current_app.logger.error('No address found for user {}'.format(user))
            return {'_status': 'error', 'message': 'letter.no-address-found'}
    except MsgTaskFailed as e:
        current_app.logger.error('Navet lookup failed for user {}: {}'.format(user, e))
        current_app.stats.count('navet_error')
        return {'_status': 'error', 'message': 'error_navet_task'}

    # Set and save official address
    proofing_state.proofing_letter.address = address
    current_app.proofing_statedb.save(proofing_state)

    try:
        campaign_id = send_letter(user, proofing_state)
    except pdf.AddressFormatException as e:
        current_app.logger.error('{!r}'.format(e.message))
        return {'_status': 'error', 'message': 'letter.bad-postal-address'}
    except EkopostException as e:
        current_app.logger.error('{!r}'.format(e.message))
        return {'_status': 'error', 'message': 'Temporary technical problems'}

    # Save the users proofing state
    proofing_state.proofing_letter.transaction_id = campaign_id
    proofing_state.proofing_letter.is_sent = True
    proofing_state.proofing_letter.sent_ts = True
    current_app.proofing_statedb.save(proofing_state)
    result = check_state(proofing_state)
    result['message'] = 'letter.saved-unconfirmed'
    return result


@letter_proofing_views.route('/verify-code', methods=['POST'])
@UnmarshalWith(schemas.VerifyCodeRequestSchema)
@MarshalWith(schemas.VerifyCodeResponseSchema)
@require_user
def verify_code(user, code):
    current_app.logger.info('Verifying code for user {}'.format(user))
    proofing_state = current_app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)

    if not proofing_state:
        return {'_status': 'error', 'message': 'letter.no_state_found'}

    # Check if provided code matches the one in the letter
    if not code == proofing_state.nin.verification_code:
        current_app.logger.error('Verification code for user {} does not match'.format(user))
        # TODO: Throttling to discourage an adversary to try brute force
        return {'_status': 'error', 'message': 'letter.wrong-code'}

    try:
        official_address = get_address(user, proofing_state)
    except MsgTaskFailed as e:
        current_app.logger.error('Navet lookup failed for user {}: {}'.format(user, e))
        current_app.stats.count('navet_error')
        return {'_status': 'error', 'message': 'error_navet_task'}

    proofing_log_entry = LetterProofing(user, created_by='eduid_letter_proofing', nin=proofing_state.nin.number,
                                        letter_sent_to=proofing_state.proofing_letter.address,
                                        transaction_id=proofing_state.proofing_letter.transaction_id,
                                        user_postal_address=official_address, proofing_version='2016v1')
    try:
        # Verify nin for user
        verify_nin_for_user(user, proofing_state, proofing_log_entry)
        current_app.logger.info('Verified code for user {}'.format(user))
        # Remove proofing state
        current_app.proofing_statedb.remove_state(proofing_state)
        return {'success': True,
                'message': 'letter.verification_success',
                'nins': user.nins.to_list_of_dicts()}
    except AmTaskFailed as e:
        current_app.logger.error('Verifying nin for user {} failed'.format(user))
        current_app.logger.error('{}'.format(e))
        return {'_status': 'error', 'message': 'Temporary technical problems'}
