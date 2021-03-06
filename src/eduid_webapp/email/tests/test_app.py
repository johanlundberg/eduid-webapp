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

import json
from mock import patch

from eduid_common.api.testing import EduidAPITestCase
from eduid_webapp.email.app import email_init_app
from eduid_userdb.mail import MailAddress


class EmailTests(EduidAPITestCase):

    def load_app(self, config):
        """
        Called from the parent class, so we can provide the appropiate flask
        app for this test case.
        """
        return email_init_app('emails', config)

    def update_config(self, config):
        config.update({
            'AVAILABLE_LANGUAGES': {'en': 'English','sv': 'Svenska'},
            'MSG_BROKER_URL': 'amqp://dummy',
            'AM_BROKER_URL': 'amqp://dummy',
            'DASHBOARD_URL': '/profile/',
            'CELERY_CONFIG': {
                'CELERY_RESULT_BACKEND': 'amqp',
                'CELERY_TASK_SERIALIZER': 'json',
                'MONGO_URI': config['MONGO_URI'],
            },
            'EMAIL_VERIFICATION_TIMEOUT': 86400,
        })
        return config

    def init_data(self):
        self.app.private_userdb.save(self.app.private_userdb.UserClass(data=self.test_user.to_dict()), check_sync=False)

    def tearDown(self):
        super(EmailTests, self).tearDown()
        with self.app.app_context():
            self.app.private_userdb._drop_whole_collection()
            self.app.proofing_statedb._drop_whole_collection()
            self.app.proofing_log._drop_whole_collection()
            self.app.central_userdb._drop_whole_collection()

    def test_get_all_emails(self):
        response = self.browser.get('/all')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']
        with self.session_cookie(self.browser, eppn) as client:
            response2 = client.get('/all')

            self.assertEqual(response.status_code, 302)

            email_data = json.loads(response2.data)

            self.assertEqual(email_data['type'], 'GET_EMAIL_ALL_SUCCESS')
            self.assertEqual(email_data['payload']['emails'][0].get('email'), 'johnsmith@example.com')
            self.assertEqual(email_data['payload']['emails'][0].get('verified'), True)
            self.assertEqual(email_data['payload']['emails'][1].get('email'), 'johnsmith2@example.com')
            self.assertEqual(email_data['payload']['emails'][1].get('verified'), False)

    def test_post_email_error_no_data(self):
        response = self.browser.post('/new')
        self.assertEqual(response.status_code, 302) # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']
        with self.session_cookie(self.browser, eppn) as client:
            response2 = client.post('/new')

            self.assertEqual(response.status_code, 302)

            new_email_data = json.loads(response2.data)
            self.assertEqual(new_email_data['type'], 'POST_EMAIL_NEW_FAIL')

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_post_email(self, mock_code_verification, mock_request_user_sync, mock_sendmail):
        response = self.browser.post('/new')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        mock_code_verification.return_value = u'123456'
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_sendmail.return_value = True
        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith3@example.com',
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/new', data=json.dumps(data),  content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                new_email_data = json.loads(response2.data)

                self.assertEqual(new_email_data['type'], 'POST_EMAIL_NEW_SUCCESS')
                self.assertEqual(new_email_data['payload']['emails'][2].get('email'), 'johnsmith3@example.com')
                self.assertEqual(new_email_data['payload']['emails'][2].get('verified'), False)

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_post_email_duplicate(self, mock_code_verification, mock_request_user_sync):
        response = self.browser.post('/new')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        mock_code_verification.return_value = u'123456'
        mock_request_user_sync.side_effect = self.request_user_sync
        eppn = self.test_user_data['eduPersonPrincipalName']
        email = 'johnsmith3@example.com'

        # Save unverified mail address for test user
        user = self.app.central_userdb.get_user_by_eppn(eppn)
        MailAddress(email=email, application='email', verified=False, primary=False)
        user.mail_addresses.add(MailAddress(email=email, application='email', verified=False, primary=False))
        self.app.central_userdb.save(user, check_sync=False)

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': email,
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/new', data=json.dumps(data),  content_type=self.content_type_json)
                self.assertEqual(response2.status_code, 200)
                new_email_data = json.loads(response2.data)

                self.assertEqual(new_email_data['type'], 'POST_EMAIL_NEW_FAIL')
                self.assertEqual(new_email_data['payload']['error']['email'][0], 'emails.duplicated')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_post_email_bad_csrf(self, mock_code_verification, mock_request_user_sync):
        response = self.browser.post('/new')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        mock_code_verification.return_value = u'123456'
        mock_request_user_sync.side_effect = self.request_user_sync
        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                data = {
                    'email': 'john-smith@example.com',
                    'verified': False,
                    'primary': False,
                    'csrf_token': 'bad_csrf'
                }

                response2 = client.post('/new', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                new_email_data = json.loads(response2.data)

                self.assertEqual(new_email_data['type'], 'POST_EMAIL_NEW_FAIL')
                self.assertEqual(new_email_data['payload']['error']['csrf_token'], ['CSRF failed to validate'])

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    def test_post_primary(self, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync

        response = self.browser.post('/primary')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/primary', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                new_email_data = json.loads(response2.data)

                self.assertEqual(new_email_data['type'], 'POST_EMAIL_PRIMARY_SUCCESS')

    def test_post_primary_missing(self):
        response = self.browser.post('/primary')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith3@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/primary', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                new_email_data = json.loads(response2.data)

                self.assertEqual(new_email_data['type'], 'POST_EMAIL_PRIMARY_FAIL')
                self.assertEqual(new_email_data['payload']['error']['email'][0], 'emails.missing')

    def test_post_primary_unconfirmed_fail(self):
        response = self.browser.post('/primary')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith2@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/primary', data=json.dumps(data),
                                        content_type=self.content_type_json)

            self.assertEqual(response2.status_code, 200)

            new_email_data = json.loads(response2.data)

            self.assertEqual(new_email_data['type'], 'POST_EMAIL_PRIMARY_FAIL')
            self.assertEqual(new_email_data['payload']['message'], 'emails.unconfirmed_address_not_primary')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    def test_remove(self, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync

        response = self.browser.post('/remove')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith2@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/remove', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                delete_email_data = json.loads(response2.data)

                self.assertEqual(delete_email_data['type'], 'POST_EMAIL_REMOVE_SUCCESS')
                self.assertEqual(delete_email_data['payload']['emails'][0].get('email'), 'johnsmith@example.com')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    def test_remove_fail(self, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync

        response = self.browser.post('/remove')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith3@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/remove', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                delete_email_data = json.loads(response2.data)

                self.assertEqual(delete_email_data['type'], 'POST_EMAIL_REMOVE_FAIL')
                self.assertEqual(delete_email_data['payload']['error']['email'][0], 'emails.missing')

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    def test_resend_code(self, mock_request_user_sync, mock_sendmail):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_sendmail.return_value = True

        response = self.browser.post('/resend-code')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/resend-code', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                delete_email_data = json.loads(response2.data)

                self.assertEqual(delete_email_data['type'], 'POST_EMAIL_RESEND_CODE_SUCCESS')
                self.assertEqual(delete_email_data['payload']['emails'][0].get('email'), 'johnsmith@example.com')
                self.assertEqual(delete_email_data['payload']['emails'][1].get('email'), 'johnsmith2@example.com')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    def test_resend_code_fails(self, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync

        response = self.browser.post('/resend-code')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:

                with self.app.test_request_context():
                    data = {
                        'email': 'johnsmith3@example.com',
                        'csrf_token': sess.get_csrf_token()
                    }

                response2 = client.post('/resend-code', data=json.dumps(data),
                                        content_type=self.content_type_json)

                self.assertEqual(response2.status_code, 200)

                resend_code_email_data = json.loads(response2.data)

                self.assertEqual(resend_code_email_data['type'], 'POST_EMAIL_RESEND_CODE_FAIL')

                self.assertEqual(resend_code_email_data['payload']['error']['email'][0], 'emails.missing')

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_verify(self, mock_code_verification, mock_request_user_sync, mock_sendmail):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_code_verification.return_value = u'432123425'
        mock_sendmail.return_value = True

        response = self.browser.post('/verify')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:
                with self.app.test_request_context():
                    data = {
                        'email': u'john-smith3@example.com',
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                client.post('/new', data=json.dumps(data),
                            content_type=self.content_type_json)

            with client.session_transaction() as sess:
                data = {
                    'email': u'john-smith3@example.com',
                    'code': u'432123425',
                    'csrf_token': sess.get_csrf_token()
                }

                response2 = client.post('/verify', data=json.dumps(data),
                                        content_type=self.content_type_json)

                verify_email_data = json.loads(response2.data)
                self.assertEqual(verify_email_data['type'], 'POST_EMAIL_VERIFY_SUCCESS')
                self.assertEqual(verify_email_data['payload']['emails'][2]['email'], u'john-smith3@example.com')
                self.assertEqual(verify_email_data['payload']['emails'][2]['verified'], True)
                self.assertEqual(verify_email_data['payload']['emails'][2]['primary'], False)
                self.assertEqual(self.app.proofing_log.db_count(), 1)

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_verify_fail(self, mock_code_verification, mock_request_user_sync, mock_sendmail):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_code_verification.return_value = u'432123425'
        mock_sendmail.return_value = True

        response = self.browser.post('/verify')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:
                with self.app.test_request_context():
                    data = {
                        'email': u'john-smith3@example.com',
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                client.post('/new', data=json.dumps(data),
                            content_type=self.content_type_json)

            with client.session_transaction() as sess:
                data = {
                    'email': u'john-smith3@example.com',
                    'code': u'not_right_code',
                    'csrf_token': sess.get_csrf_token()
                }

                response2 = client.post('/verify', data=json.dumps(data),
                                        content_type=self.content_type_json)

                verify_email_data = json.loads(response2.data)
                self.assertEqual(verify_email_data['type'], 'POST_EMAIL_VERIFY_FAIL')
                self.assertEqual(verify_email_data['payload']['message'], 'emails.code_invalid_or_expired')
                self.assertEqual(self.app.proofing_log.db_count(), 0)

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_verify_email_link(self, mock_code_verification, mock_request_user_sync, mock_sendmail):
        mock_code_verification.return_value = u'432123425'
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_sendmail.return_value = True
        email = 'johnsmith3@example.com'

        response = self.browser.post('/verify')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:
                with self.app.test_request_context():
                    data = {
                        'email': email,
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                client.post('/new', data=json.dumps(data),
                            content_type=self.content_type_json)

            with client.session_transaction():
                code = '432123425'
                response2 = client.get('/verify?code={}&email={}'.format(code, email))

                self.assertEqual(response2.status_code, 302)
                self.assertEqual(response2.location,
                                 'http://test.localhost/profile/emails?msg=emails.verification-success')

                user = self.app.private_userdb.get_user_by_eppn(eppn)
                mail_address_element = user.mail_addresses.find(email)
                self.assertTrue(mail_address_element)

                self.assertEqual(mail_address_element.email, email)
                self.assertEqual(mail_address_element.is_verified, True)
                self.assertEqual(mail_address_element.is_primary, False)
                self.assertEqual(self.app.proofing_log.db_count(), 1)

    @patch('eduid_common.api.mail_relay.MailRelay.sendmail')
    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_webapp.email.verifications.get_unique_hash')
    def test_verify_email_link_fail(self, mock_code_verification, mock_request_user_sync, mock_sendmail):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_code_verification.return_value = u'432123425'
        mock_sendmail.return_value = True
        email = 'johnsmith3@example.com'

        response = self.browser.post('/verify')
        self.assertEqual(response.status_code, 302)  # Redirect to token service

        eppn = self.test_user_data['eduPersonPrincipalName']

        with self.session_cookie(self.browser, eppn) as client:
            with client.session_transaction() as sess:
                with self.app.test_request_context():
                    data = {
                        'email': email,
                        'verified': False,
                        'primary': False,
                        'csrf_token': sess.get_csrf_token()
                    }

                client.post('/new', data=json.dumps(data),
                            content_type=self.content_type_json)

            with client.session_transaction():
                code = 'not_right_code'
                response2 = client.get('/verify?code={}&email={}'.format(code, email))

                self.assertEqual(response2.status_code, 302)
                self.assertEqual(response2.location,
                                 'http://test.localhost/profile/emails?msg=%3AERROR%3Aemails.code_invalid_or_expired')

                user = self.app.private_userdb.get_user_by_eppn(eppn)
                mail_address_element = user.mail_addresses.find(email)
                self.assertTrue(mail_address_element)

                self.assertEqual(mail_address_element.email, email)
                self.assertEqual(mail_address_element.is_verified, False)
                self.assertEqual(mail_address_element.is_primary, False)
                self.assertEqual(self.app.proofing_log.db_count(), 0)
