# -*- coding: utf-8 -*-
from __future__ import absolute_import

from flask import Blueprint, current_app, request, render_template
from eduid_common.api.decorators import require_support_personnel
from eduid_userdb.exceptions import UserHasUnknownData, UserHasNotCompletedSignup

support_views = Blueprint('support', __name__, url_prefix='', template_folder='templates')


@support_views.route('/', methods=['GET', 'POST'])
@require_support_personnel
def index(support_user):
    if request.method == 'POST':
        search_query = request.form.get('query')
        lookup_users = current_app.support_user_db.search_users(request.form.get('query'))
        users = list()

        if len(lookup_users) == 0:
            # If no users where found in the central database look in signup database
            lookup_users = current_app.support_signup_db.get_user_by_mail(search_query, raise_on_missing=False,
                                                                          return_list=True, include_unconfirmed=True)
            if len(lookup_users) == 0:
                user = current_app.support_signup_db.get_user_by_pending_mail_address(search_query)
                if user:
                    lookup_users = [user]
            if len(lookup_users) == 0:
                current_app.logger.warn('Support personnel: {!r} searched for {!r} without any match found'
                                        .format(support_user, search_query))
                return render_template('index.html', support_user=support_user,
                                       error="No users matched the search query")

        current_app.logger.info('Support personnel: {!r} searched for {!r}'.format(support_user, search_query))
        for user in lookup_users:
            user_data = dict()
            # Users
            try:
                user_data['user'] = current_app.support_user_db.get_user_by_id(user_id=user['user_id'],
                                                                               raise_on_missing=False)
            except UserHasNotCompletedSignup:
                # This should not happen as uncompleted users should not be propagated but seems we had a bug in 2015
                user_data['user'] = None
            try:
                user_data['signup_user'] = current_app.support_signup_db.get_user_by_id(user_id=user['user_id'],
                                                                                        raise_on_missing=False)
            except UserHasUnknownData:
                # The user has completed signup but is in an old format, disregard
                user_data['signup_user'] = None

            # Aux data
            user_data['authn'] = current_app.support_authn_db.get_authn_info(user_id=user['user_id'])
            user_data['proofing_log'] = current_app.support_proofing_log_db.get_entries(
                eppn=user['eduPersonPrincipalName'])
            user_data['actions'] = current_app.support_actions_db.get_actions(user_id=user['user_id'])
            user_data['letter_proofing'] = current_app.support_letter_proofing_db.get_proofing_state(
                eppn=user['eduPersonPrincipalName'])
            user_data['oidc_proofing'] = current_app.support_oidc_proofing_db.get_proofing_state(
                eppn=user['eduPersonPrincipalName'])
            user_data['email_proofing'] = current_app.support_email_proofing_db.get_proofing_state(
                eppn=user['eduPersonPrincipalName'])
            user_data['phone_proofing'] = current_app.support_phone_proofing_db.get_proofing_state(
                eppn=user['eduPersonPrincipalName'])
            users.append(user_data)

        return render_template('index.html', support_user=support_user, users=users, search_query=search_query)
    else:
        return render_template('index.html', support_user=support_user)
