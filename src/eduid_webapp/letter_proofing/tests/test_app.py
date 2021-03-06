# -*- coding: utf-8 -*-

from __future__ import absolute_import

from os import devnull
import json
from datetime import datetime
from collections import OrderedDict
from mock import patch
from bson import ObjectId

from eduid_userdb.data_samples import NEW_UNVERIFIED_USER_EXAMPLE
from eduid_userdb.user import User
from eduid_userdb.locked_identity import LockedIdentityNin
from eduid_userdb.nin import Nin
from eduid_common.api.testing import EduidAPITestCase
from eduid_webapp.letter_proofing.app import init_letter_proofing_app

__author__ = 'lundberg'


class LetterProofingTests(EduidAPITestCase):
    """Base TestCase for those tests that need a full environment setup"""

    def setUp(self):
        super(LetterProofingTests, self).setUp()

        self.test_user_eppn = 'hubba-baar'
        self.test_user_nin = '200001023456'
        self.test_user_wrong_nin = '190001021234'
        self.mock_address = OrderedDict([
            (u'Name', OrderedDict([
                (u'GivenNameMarking', u'20'), (u'GivenName', u'Testaren Test'),
                (u'Surname', u'Testsson')])),
            (u'OfficialAddress', OrderedDict([(u'Address2', u'\xd6RGATAN 79 LGH 10'),
                                              (u'PostalCode', u'12345'),
                                              (u'City', u'LANDET')]))
        ])

    def load_app(self, config):
        """
        Called from the parent class, so we can provide the appropriate flask
        app for this test case.
        """
        return init_letter_proofing_app('testing', config)

    def init_data(self):
        """
        Called from the parent class, so we can extend data initialized.
        """
        test_user = User(data=NEW_UNVERIFIED_USER_EXAMPLE)  # eppn hubba-baar
        self.app.central_userdb.save(test_user, check_sync=False)

    def update_config(self, config):
        config.update({
            'EKOPOST_DEBUG_PDF': devnull,
            'LETTER_WAIT_TIME_HOURS': 336,
            'MSG_BROKER_URL': 'amqp://dummy',
            'AM_BROKER_URL': 'amqp://dummy',
            'CELERY_CONFIG': {
                'CELERY_RESULT_BACKEND': 'amqp',
                'CELERY_TASK_SERIALIZER': 'json',
                'MONGO_URI': config['MONGO_URI']
            },
        })
        return config

    def tearDown(self):
        super(LetterProofingTests, self).tearDown()
        with self.app.app_context():
            self.app.central_userdb._drop_whole_collection()
            self.app.private_userdb._drop_whole_collection()
            self.app.proofing_statedb._drop_whole_collection()
            self.app.proofing_log._drop_whole_collection()

    # Helper methods
    def get_state(self):
        with self.session_cookie(self.browser, self.test_user_eppn) as client:
            response = client.get('/proofing')
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def send_letter(self, nin, csrf_token, mock_get_postal_address, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_get_postal_address.return_value = self.mock_address
        data = {'nin': nin, 'csrf_token': csrf_token}
        with self.session_cookie(self.browser, self.test_user_eppn) as client:
            response = client.post('/proofing', data=json.dumps(data), content_type=self.content_type_json)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def verify_code(self, code, csrf_token, mock_get_postal_address, mock_request_user_sync):
        mock_request_user_sync.side_effect = self.request_user_sync
        mock_get_postal_address.return_value = self.mock_address
        data = {'code': code, 'csrf_token': csrf_token}
        with self.session_cookie(self.browser, self.test_user_eppn) as client:
            response = client.post('/verify-code', data=json.dumps(data), content_type=self.content_type_json)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)
    # End helper methods

    def test_authenticate(self):
        response = self.browser.get('/proofing')
        self.assertEqual(response.status_code, 302)  # Redirect to token service
        with self.session_cookie(self.browser, self.test_user_eppn) as client:
            response = client.get('/proofing')
        self.assertEqual(response.status_code, 200)  # Authenticated request

    def test_letter_not_sent_status(self):
        json_data = self.get_state()
        self.assertNotIn('letter_sent', json_data)

    def test_send_letter(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        json_data = self.send_letter(self.test_user_nin, csrf_token)
        expires = json_data['payload']['letter_expires']
        expires = datetime.utcfromtimestamp(int(expires))
        self.assertIsInstance(expires, datetime)
        expires = expires.strftime('%Y-%m-%d')
        self.assertIsInstance(expires, str)

    def test_send_letter_bad_csrf(self):
        json_data = self.send_letter(self.test_user_nin, 'bad_csrf')
        self.assertEqual(json_data['type'], 'POST_LETTER_PROOFING_PROOFING_FAIL')
        self.assertEqual(json_data['payload']['error']['csrf_token'], ['CSRF failed to validate'])

    def test_letter_sent_status(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        self.send_letter(self.test_user_nin, csrf_token)
        json_data = self.get_state()
        self.assertIn('letter_sent', json_data['payload'])
        expires = datetime.utcfromtimestamp(int(json_data['payload']['letter_expires']))
        self.assertIsInstance(expires, datetime)
        expires = expires.strftime('%Y-%m-%d')
        self.assertIsInstance(expires, str)

    def test_verify_letter_code(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        json_data = self.send_letter(self.test_user_nin, csrf_token)
        with self.app.test_request_context():
            with self.app.app_context():
                proofing_state = self.app.proofing_statedb.get_state_by_eppn(self.test_user_eppn,
                                                                             raise_on_missing=False)
        csrf_token = json_data['payload']['csrf_token']
        json_data = self.verify_code(proofing_state.nin.verification_code, csrf_token)
        self.assertTrue(json_data['payload']['success'])

        user = self.app.private_userdb.get_user_by_eppn(self.test_user_eppn)
        self.assertEqual(user.nins.primary.number, self.test_user_nin)
        self.assertEqual(user.nins.primary.created_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.verified_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.is_verified, True)
        self.assertEqual(self.app.proofing_log.db_count(), 1)

    def test_verify_letter_code_bad_csrf(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        self.send_letter(self.test_user_nin, csrf_token)
        with self.app.test_request_context():
            with self.app.app_context():
                proofing_state = self.app.proofing_statedb.get_state_by_eppn(self.test_user_eppn,
                                                                             raise_on_missing=False)
        json_data = self.verify_code(proofing_state.nin.verification_code, 'bad_csrf')
        self.assertEqual(json_data['type'], 'POST_LETTER_PROOFING_VERIFY_CODE_FAIL')
        self.assertEqual(json_data['payload']['error']['csrf_token'], ['CSRF failed to validate'])

    def test_verify_letter_code_fail(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        json_data = self.send_letter(self.test_user_nin, csrf_token)
        csrf_token = json_data['payload']['csrf_token']
        json_data = self.verify_code('wrong code', csrf_token)
        self.assertEqual(json_data['payload']['message'], 'letter.wrong-code')

    def test_proofing_flow(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        self.send_letter(self.test_user_nin, csrf_token)
        self.get_state()
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        with self.app.test_request_context():
            user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn, raise_on_missing=True)
            proofing_state = self.app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)
        json_data = self.verify_code(proofing_state.nin.verification_code, csrf_token)
        self.assertTrue(json_data['payload']['success'])

        user = self.app.private_userdb.get_user_by_eppn(self.test_user_eppn)
        self.assertEqual(user.nins.primary.number, self.test_user_nin)
        self.assertEqual(user.nins.primary.created_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.verified_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.is_verified, True)
        self.assertEqual(self.app.proofing_log.db_count(), 1)

    def test_proofing_flow_previously_added_nin(self):
        user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn)
        not_verified_nin = Nin(number=self.test_user_nin, application='test', verified=False, primary=False)
        user.nins.add(not_verified_nin)
        self.app.central_userdb.save(user)

        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        self.send_letter(self.test_user_nin, csrf_token)
        self.get_state()
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        with self.app.test_request_context():
            user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn, raise_on_missing=True)
            proofing_state = self.app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)
        json_data = self.verify_code(proofing_state.nin.verification_code, csrf_token)
        self.assertTrue(json_data['payload']['success'])

        user = self.app.private_userdb.get_user_by_eppn(self.test_user_eppn)
        self.assertEqual(user.nins.primary.number, self.test_user_nin)
        self.assertEqual(user.nins.primary.created_by, not_verified_nin.created_by)
        self.assertEqual(user.nins.primary.verified_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.is_verified, True)
        self.assertEqual(self.app.proofing_log.db_count(), 1)

    def test_proofing_flow_previously_added_wrong_nin(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        # Send letter to correct nin
        self.send_letter(self.test_user_nin, csrf_token)

        # Remove correct unverified nin and add wrong nin
        user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn)
        user.nins.remove(self.test_user_nin)
        not_verified_nin = Nin(number=self.test_user_wrong_nin, application='test', verified=False, primary=False)
        user.nins.add(not_verified_nin)
        self.app.central_userdb.save(user)

        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        with self.app.test_request_context():
            user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn, raise_on_missing=True)
            proofing_state = self.app.proofing_statedb.get_state_by_eppn(user.eppn, raise_on_missing=False)
        json_data = self.verify_code(proofing_state.nin.verification_code, csrf_token)
        self.assertTrue(json_data['payload']['success'])

        user = self.app.private_userdb.get_user_by_eppn(self.test_user_eppn)
        self.assertEqual(user.nins.primary.number, self.test_user_nin)
        self.assertEqual(user.nins.primary.created_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.verified_by, proofing_state.nin.created_by)
        self.assertEqual(user.nins.primary.is_verified, True)
        self.assertEqual(self.app.proofing_log.db_count(), 1)

    def test_expire_proofing_state(self):
        json_data = self.get_state()
        csrf_token = json_data['payload']['csrf_token']
        self.send_letter(self.test_user_nin, csrf_token)
        json_data = self.get_state()
        self.assertIn('letter_sent', json_data['payload'])
        self.app.config.update({'LETTER_WAIT_TIME_HOURS': -24})
        json_data = self.get_state()
        self.assertTrue(json_data['payload']['letter_expired'])
        self.assertIn('letter_sent', json_data['payload'])
        self.assertIsNotNone(json_data['payload']['letter_sent'])

    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def test_unmarshal_error(self, mock_get_postal_address):
        mock_get_postal_address.return_value = self.mock_address
        data = {'nin': 'not a nin'}
        with self.session_cookie(self.browser, self.test_user_eppn) as client:
            response = client.post('/proofing', data=json.dumps(data), content_type=self.content_type_json)
        self.assertEqual(response.status_code, 200)
        json_data = json.loads(response.data)
        self.assertEqual(json_data['type'], 'POST_LETTER_PROOFING_PROOFING_FAIL')
        self.assertIn('nin', json_data['payload']['error'].keys())

    def test_deprecated_proofing_state(self):
        deprecated_data = {
            'user_id': ObjectId('012345678901234567890123'),
            'nin': {
                'created_by': 'eduid-userdb.tests',
                'created_ts': datetime(2015, 11, 9, 12, 53, 9, 708761),
                'number': '200102034567',
                'verification_code': 'abc123',
                'verified': False
            },
            'proofing_letter': {
                'is_sent': False,
                'sent_ts': None,
                'transaction_id': None,
                'address': self.mock_address
            }
        }
        with self.app.app_context():
            self.app.proofing_statedb._coll.insert(deprecated_data)
            state = self.app.proofing_statedb.get_state_by_user_id('012345678901234567890123', self.test_user_eppn)
        self.assertIsNotNone(state)
        state_dict = state.to_dict()
        self.assertItemsEqual(state_dict.keys(), ['_id', 'eduPersonPrincipalName', 'nin', 'proofing_letter',
                                                  'modified_ts'])
        self.assertItemsEqual(state_dict['nin'].keys(), ['created_by', 'created_ts', 'number', 'verification_code',
                                                         'verified'])
        self.assertItemsEqual(state_dict['proofing_letter'].keys(), ['is_sent', 'sent_ts', 'transaction_id',
                                                                     'address'])

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def test_locked_identity_no_locked_identity(self, mock_get_postal_address, mock_request_user_sync):
        mock_get_postal_address.return_value = self.mock_address
        mock_request_user_sync.side_effect = self.request_user_sync
        user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn)
        self.assertEqual(user.locked_identity.count, 0)

        # User with no locked_identity
        with self.session_cookie(self.browser, self.test_user_eppn):
            json_data = self.get_state()
            csrf_token = json_data['payload']['csrf_token']
            response = self.send_letter(self.test_user_nin, csrf_token)
        self.assertEqual(response['type'], 'POST_LETTER_PROOFING_PROOFING_SUCCESS')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def test_locked_identity_correct_nin(self, mock_get_postal_address, mock_request_user_sync):
        mock_get_postal_address.return_value = self.mock_address
        mock_request_user_sync.side_effect = self.request_user_sync
        user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn)

        # User with locked_identity and correct nin
        user.locked_identity.add(LockedIdentityNin(number=self.test_user_nin, created_by='test', created_ts=True))
        self.app.central_userdb.save(user, check_sync=False)
        with self.session_cookie(self.browser, self.test_user_eppn):
            json_data = self.get_state()
            csrf_token = json_data['payload']['csrf_token']
            response = self.send_letter(self.test_user_nin, csrf_token)
        self.assertEqual(response['type'], 'POST_LETTER_PROOFING_PROOFING_SUCCESS')

    @patch('eduid_common.api.am.AmRelay.request_user_sync')
    @patch('eduid_common.api.msg.MsgRelay.get_postal_address')
    def test_locked_identity_incorrect_nin(self, mock_get_postal_address, mock_request_user_sync):
        mock_get_postal_address.return_value = self.mock_address
        mock_request_user_sync.side_effect = self.request_user_sync
        user = self.app.central_userdb.get_user_by_eppn(self.test_user_eppn)

        user.locked_identity.add(LockedIdentityNin(number=self.test_user_nin, created_by='test', created_ts=True))
        self.app.central_userdb.save(user, check_sync=False)

        # User with locked_identity and incorrect nin
        with self.session_cookie(self.browser, self.test_user_eppn):
            json_data = self.get_state()
            csrf_token = json_data['payload']['csrf_token']
            response = self.send_letter('200102031234', csrf_token)
        self.assertEqual(response['type'], 'POST_LETTER_PROOFING_PROOFING_FAIL')
