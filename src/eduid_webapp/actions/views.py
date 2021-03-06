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
import json

from flask import Blueprint, request, session, current_app
from flask import abort, url_for, render_template

from eduid_userdb.actions import Action
from eduid_common.api.decorators import MarshalWith, UnmarshalWith
from eduid_common.api.schemas.base import FluxStandardAction
from eduid_webapp.authn.helpers import verify_auth_token
from eduid_webapp.actions.helpers import get_next_action

actions_views = Blueprint('actions', __name__, url_prefix='', template_folder='templates')


@actions_views.route('/', methods=['GET'])
def authn():
    '''
    '''
    userid = request.args.get('userid', None)
    token = request.args.get('token', None)
    nonce = request.args.get('nonce', None)
    timestamp = request.args.get('ts', None)
    idp_session = request.args.get('session', None)
    if not (userid and token and nonce and timestamp):
        msg = ('Insufficient authentication params: '
               'userid: {}, token: {}, nonce: {}, ts: {}')
        current_app.logger.debug(msg.format(userid, token, nonce, timestamp))
        abort(400)

    if verify_auth_token(eppn=userid, token=token,
                         nonce=nonce, timestamp=timestamp):
        current_app.logger.info("Starting pre-login actions "
                                "for userid: {})".format(userid))
        session['userid'] = userid
        session['idp_session'] = idp_session
        url = url_for('actions.get_actions')
        return render_template('index.html', url=url)
    else:
        current_app.logger.debug("Token authentication failed "
                                 "(userid: {})".format(userid))
        abort(403)


@actions_views.route('/config', methods=['GET'])
@MarshalWith(FluxStandardAction)
def get_config():
    try:
        action_type = session['current_plugin']
    except KeyError:
        abort(403)
    plugin_obj = current_app.plugins[action_type]()
    action = Action(data=session['current_action'])
    try:
        config = plugin_obj.get_config_for_bundle(action)
        config['csrf_token'] = session.new_csrf_token()
        return config
    except plugin_obj.ActionError as exc:
        return {
            '_status': 'error',
            'message':  exc.args[0]
            }


@actions_views.route('/get-actions', methods=['GET'])
def get_actions():
    actions = get_next_action()
    if not actions['action']:
        return json.dumps({'action': False,
                           'url': actions['idp_url'],
                           'payload': {
                               'csrf_token': session.new_csrf_token()
                               }
                           })
    action_type = session['current_plugin']
    plugin_obj = current_app.plugins[action_type]()
    action = Action(data=session['current_action'])
    current_app.logger.info('Starting pre-login action {} '
                            'for userid {}'.format(action.action_type,
                                                    session['userid']))
    try:
        url = plugin_obj.get_url_for_bundle(action)
        return json.dumps({'action': True,
                           'url': url,
                           'payload': {
                               'csrf_token': session.new_csrf_token()
                               }})
    except plugin_obj.ActionError as exc:
        _aborted(action, exc)
        abort(500)


@actions_views.route('/post-action', methods=['POST'])
@MarshalWith(FluxStandardAction)
def post_action():
    if not request.data or \
            session.get_csrf_token() != json.loads(request.data)['csrf_token']:
        abort(400)
    try:
        action_type = session['current_plugin']
    except KeyError:
        abort(403)
    plugin_obj = current_app.plugins[action_type]()
    action = Action(data=session['current_action'])
    errors = {}
    try:
        data = plugin_obj.perform_step(action)
    except plugin_obj.ActionError as exc:
        return _aborted(action, exc)

    except plugin_obj.ValidationError as exc:
        errors = exc.args[0]
        current_app.logger.info('Validation error {0} '
                    'for step {1} of action {2}'.format(
                        str(errors),
                        str(session['current_step']),
                        str(action)))
        session['current_step'] -= 1
        return {
                '_status': 'error',
                'errors': errors,
                'csrf_token': session.new_csrf_token()
                }

    if session['total_steps'] == session['current_step']:
        current_app.logger.info('Finished pre-login action {0} '
                                'for userid {1}'.format(action.action_type,
                                                        session['userid']))
        return {
                'message': 'actions.action-completed',
                'data': data,
                'csrf_token': session.new_csrf_token()
                }

    current_app.logger.info('Performed step {} for action {} '
                            'for userid {}'.format(action.action_type,
                                                    session['current_step'],
                                                    session['userid']))
    session['current_step'] += 1

    return {
            'data': data,
            'csrf_token': session.new_csrf_token()
            }


def _aborted(action, exc):
    current_app.logger.info(u'Aborted pre-login action {} for userid {}, '
                            u'reason: {}'.format(action.action_type,
                                                 session['userid'],
                                                 exc.args[0]))
    if exc.remove_action:
        aid = action.action_id
        msg = 'Removing faulty action with id '
        current_app.logger.info(msg + str(aid))
        current_app.actions_db.remove_action_by_id(aid)
    return {
            '_status': 'error',
            'message': exc.args[0]
            }
