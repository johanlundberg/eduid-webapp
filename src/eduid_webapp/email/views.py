# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 NORDUnet A/S
# All rights reserved.
#
#   Redistribution and use in source and binary forms, with or
#   without modification, are permitted provided that the following
#   conditions are met:
#
#     1. Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#     2. Redistributions in binary form must reproduce the above
#        copyright notice, this list of conditions and the following
#        disclaimer in the documentation and/or other materials provided
#        with the distribution.
#     3. Neither the name of the NORDUnet nor the names of its
#        contributors may be used to endorse or promote products derived
#        from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
from __future__ import absolute_import
import urlparse
from urllib import urlencode

from flask import Blueprint, request, current_app, redirect, abort

from eduid_userdb.element import PrimaryElementViolation, DuplicateElementViolation
from eduid_userdb.exceptions import UserOutOfSync, DocumentDoesNotExist
from eduid_userdb.mail import MailAddress
from eduid_userdb.proofing import ProofingUser
from eduid_common.api.decorators import require_user, MarshalWith, UnmarshalWith
from eduid_common.api.utils import save_and_sync_user
from eduid_common.api.utils import urlappend
from eduid_webapp.email.schemas import EmailListPayload, AddEmailSchema
from eduid_webapp.email.schemas import ChangeEmailSchema, EmailResponseSchema
from eduid_webapp.email.schemas import VerificationCodeSchema
from eduid_webapp.email.verifications import send_verification_code, verify_mail_address

email_views = Blueprint('email', __name__, url_prefix='', template_folder='templates')


@email_views.route('/all', methods=['GET'])
@MarshalWith(EmailResponseSchema)
@require_user
def get_all_emails(user):
    emails = {
        'emails': user.mail_addresses.to_list_of_dicts(),
        'message': 'emails.get-success'
    }

    return EmailListPayload().dump(emails).data


@email_views.route('/new', methods=['POST'])
@UnmarshalWith(AddEmailSchema)
@MarshalWith(EmailResponseSchema)
@require_user
def post_email(user, email, verified, primary):
    proofing_user = ProofingUser.from_user(user, current_app.private_userdb)
    current_app.logger.debug('Trying to save unconfirmed email {!r} '
                             'for user {}'.format(email, proofing_user))

    new_mail = MailAddress(email=email, application='email',
                           verified=False, primary=False)

    try:
        proofing_user.mail_addresses.add(new_mail)
    except DuplicateElementViolation:
        return {
            '_status': 'error',
            'message':  'emails.duplicated'
        }

    try:
        save_and_sync_user(proofing_user)
    except UserOutOfSync:
        current_app.logger.debug('Couldnt save email {} for user {}, '
                                 'data out of sync'.format(email, proofing_user))
        return {
            '_status': 'error',
            'message': 'user-out-of-sync'
        }
    current_app.logger.info('Saved unconfirmed email {!r} '
                            'for user {}'.format(email, proofing_user))
    current_app.stats.count(name='email_save_unconfirmed_email', value=1)

    send_verification_code(email, proofing_user)
    current_app.stats.count(name='email_send_verification_code', value=1)

    emails = {
            'emails': proofing_user.mail_addresses.to_list_of_dicts(),
            'message': 'emails.save-success'
            }
    return EmailListPayload().dump(emails).data


@email_views.route('/primary', methods=['POST'])
@UnmarshalWith(ChangeEmailSchema)
@MarshalWith(EmailResponseSchema)
@require_user
def post_primary(user, email):
    proofing_user = ProofingUser.from_user(user, current_app.private_userdb)
    current_app.logger.debug('Trying to save email address {!r} as primary '
                             'for user {}'.format(email, proofing_user))

    try:
        mail = proofing_user.mail_addresses.find(email)
    except IndexError:
        current_app.logger.debug('Couldnt save email {!r} as primary for user'
                                 ' {}, data out of sync'.format(email, proofing_user))
        return {
            '_status': 'error',
            'message': 'user-out-of-sync'
        }

    if not mail.is_verified:
        current_app.logger.debug('Couldnt save email {!r} as primary for user'
                                 ' {}, email unconfirmed'.format(email, proofing_user))
        return {
            '_status': 'error',
            'message': 'emails.unconfirmed_address_not_primary'
        }

    proofing_user.mail_addresses.primary = mail.email
    try:
        save_and_sync_user(proofing_user)
    except UserOutOfSync:
        current_app.logger.debug('Couldnt save email {!r} as primary for user'
                                 ' {}, data out of sync'.format(email, proofing_user))
        return {
            '_status': 'error',
            'message': 'user-out-of-sync'
        }
    current_app.logger.info('Email address {!r} made primary '
                            'for user {}'.format(email, proofing_user))
    current_app.stats.count(name='email_set_primary', value=1)

    emails = {
            'emails': proofing_user.mail_addresses.to_list_of_dicts(),
            'message': 'emails.primary-success'
            }
    return EmailListPayload().dump(emails).data


@email_views.route('/verify', methods=['POST'])
@UnmarshalWith(VerificationCodeSchema)
@MarshalWith(EmailResponseSchema)
@require_user
def verify(user, code, email):
    """
    """
    proofing_user = ProofingUser.from_user(user, current_app.private_userdb)
    current_app.logger.debug('Trying to save email address {} as verified'.format(email))

    db = current_app.proofing_statedb
    try:
        state = db.get_state_by_eppn_and_email(proofing_user.eppn, email)
        timeout = current_app.config['EMAIL_VERIFICATION_TIMEOUT']
        if state.is_expired(timeout):
            current_app.logger.info("Verification code is expired. Removing the state")
            current_app.logger.debug("Proofing state: {}".format(state))
            current_app.proofing_statedb.remove_state(state)
            return {
                '_status': 'error',
                'message': 'emails.code_invalid_or_expired'
            }
    except DocumentDoesNotExist:
        current_app.logger.info('Could not find proofing state for email {}'.format(email))
        return {
            '_status': 'error',
            'message': 'emails.unknown_email'
        }

    if code == state.verification.verification_code:
        try:
            verify_mail_address(state, proofing_user)
            current_app.logger.info('Email successfully verified')
            current_app.logger.debug('Email address: {}'.format(email))
            emails = {
                'emails': proofing_user.mail_addresses.to_list_of_dicts(),
                'message': 'emails.verification-success'
            }
            return EmailListPayload().dump(emails).data
        except UserOutOfSync:
            current_app.logger.info('Could not confirm email, data out of sync')
            current_app.logger.debug('Mail address: {}'.format(email))
            return {
                '_status': 'error',
                'message': 'user-out-of-sync'
            }
    current_app.logger.info("Invalid verification code")
    current_app.logger.debug("Email address: {}".format(state.verification.email))
    return {
        '_status': 'error',
        'message': 'emails.code_invalid_or_expired'
    }


@email_views.route('/verify', methods=['GET'])
@require_user
def verify_link(user):
    """
    Used for verifying an e-mail address when the user clicks the link in the verification mail.
    """
    proofing_user = ProofingUser.from_user(user, current_app.private_userdb)
    code = request.args.get('code')
    email = request.args.get('email')
    if code and email:
        current_app.logger.debug('Trying to save email address {} as verified for user {}'.format(email, proofing_user))
        url = urlappend(current_app.config['DASHBOARD_URL'], 'emails')
        scheme, netloc, path, query_string, fragment = urlparse.urlsplit(url)

        try:
            state = current_app.proofing_statedb.get_state_by_eppn_and_email(proofing_user.eppn, email)
            timeout = current_app.config.get('EMAIL_VERIFICATION_TIMEOUT', 24)
            if state.is_expired(timeout):
                current_app.logger.info("Verification code is expired. Removing the state")
                current_app.logger.debug("Proofing state: {}".format(state))
                current_app.proofing_statedb.remove_state(state)
                new_query_string = urlencode({'msg': ':ERROR:emails.code_invalid_or_expired'})
                url = urlparse.urlunsplit((scheme, netloc, path, new_query_string, fragment))
                return redirect(url)
        except DocumentDoesNotExist:
            current_app.logger.info('Could not find proofing state for email {}'.format(email))
            new_query_string = urlencode({'msg': ':ERROR:emails.unknown_email'})
            url = urlparse.urlunsplit((scheme, netloc, path, new_query_string, fragment))
            return redirect(url)

        if code == state.verification.verification_code:
            try:
                verify_mail_address(state, proofing_user)
                current_app.logger.info('Email successfully verified')
                current_app.logger.debug('Email address: {}'.format(email))
                new_query_string = urlencode({'msg': 'emails.verification-success'})
                url = urlparse.urlunsplit((scheme, netloc, path, new_query_string, fragment))
                return redirect(url)
            except UserOutOfSync:
                current_app.logger.info('Could not confirm email, data out of sync')
                current_app.logger.debug('Mail address: {}'.format(email))
                new_query_string = urlencode({'msg': ':ERROR:user-out-of-sync'})
                url = urlparse.urlunsplit((scheme, netloc, path, new_query_string, fragment))
                return redirect(url)
        current_app.logger.info("Invalid verification code")
        current_app.logger.debug("Email address: {}".format(state.verification.email))
        new_query_string = urlencode({'msg': ':ERROR:emails.code_invalid_or_expired'})
        url = urlparse.urlunsplit((scheme, netloc, path, new_query_string, fragment))
        return redirect(url)
    abort(400)


@email_views.route('/remove', methods=['POST'])
@UnmarshalWith(ChangeEmailSchema)
@MarshalWith(EmailResponseSchema)
@require_user
def post_remove(user, email):
    proofing_user = ProofingUser.from_user(user, current_app.private_userdb)
    current_app.logger.debug('Trying to remove email address {!r} '
                             'from user {}'.format(email, proofing_user))

    emails = proofing_user.mail_addresses.to_list()
    if len(emails) == 1:
        msg = "Cannot remove unique address: {!r}".format(email)
        current_app.logger.debug(msg)
        return {
            '_status': 'error',
            'message': 'emails.cannot_remove_unique'
        }

    try:
        proofing_user.mail_addresses.remove(email)
    except PrimaryElementViolation:
        new_index = 1 if emails[0].email == email else 0
        proofing_user.mail_addresses.primary = emails[new_index].email
        proofing_user.mail_addresses.remove(email)

    try:
        save_and_sync_user(proofing_user)
    except UserOutOfSync:
        current_app.logger.debug('Couldnt remove email {!r} for user'
                                 ' {}, data out of sync'.format(email, proofing_user))
        return {
            '_status': 'error',
            'message': 'user-out-of-sync'
        }

    except PrimaryElementViolation:
        return {
            '_status': 'error',
            'message': 'emails.cannot_remove_primary'
        }

    current_app.logger.info('Email address {!r} removed '
                            'for user {}'.format(email, proofing_user))
    current_app.stats.count(name='email_remove_success', value=1)

    emails = {
            'emails': proofing_user.mail_addresses.to_list_of_dicts(),
            'message': 'emails.removal-success'
            }
    return EmailListPayload().dump(emails).data


@email_views.route('/resend-code', methods=['POST'])
@UnmarshalWith(ChangeEmailSchema)
@MarshalWith(EmailResponseSchema)
@require_user
def resend_code(user, email):
    current_app.logger.debug('Trying to send new verification code for email '
                             'address {} for user {}'.format(email, user))

    if not user.mail_addresses.find(email):
        current_app.logger.debug('Unknown email {!r} in resend_code_action,'
                                 ' user {}'.format(email, user))
        return {
            '_status': 'error',
            'message': 'user-out-of-sync'
        }
    
    send_verification_code(email, user)
    current_app.logger.debug('New verification code sended to '
                             'address {} for user {}'.format(email, user))
    current_app.stats.count(name='email_resend_code', value=1)

    emails = {
            'emails': user.mail_addresses.to_list_of_dicts(),
            'message': 'emails.code-sent'
            }
    return EmailListPayload().dump(emails).data
