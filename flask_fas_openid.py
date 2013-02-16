# -*- coding: utf-8 -*-
# Flask-FAS-OpenID - A Flask extension for authorizing users with FAS-OpenID
#
# Primary maintainer: Patrick Uiterwijk <puiterwijk@fedoraproject.org>
#
# Copyright (c) 2013, Patrick Uiterwijk
# This file is part of python-fedora
#
# python-fedora is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# python-fedora is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with python-fedora; if not, see <http://www.gnu.org/licenses/>

'''
FAS-OpenID authentication plugin for the flask web framework

.. moduleauthor:: Patrick Uiterwijk <puiterwijk@fedoraproject.org>

..versionadded:: 0.3.XX
'''
from functools import wraps

from bunch import Bunch
import flask
try:
    from flask import _app_ctx_stack as stack
except ImportError:
    from flask import _request_ctx_stack as stack

import openid
from openid.consumer import consumer
from openid.fetchers import setDefaultFetcher, Urllib2Fetcher
from openid.extensions import pape, sreg

from fedora import __version__
import fedora._openid_extensions.openid_teams as teams
import fedora._openid_extensions.openid_cla as cla

class FAS(object):

    def __init__(self, app=None):
        self.app = app
        if self.app is not None:
            self._init_app(app)

    def _init_app(self, app):
        app.config.setdefault('FAS_OPENID_ENDPOINT',
                              'http://id.fedoraproject.org/')
        app.config.setdefault('FAS_OPENID_CHECK_CERT', True)

        if not self.app.config['FAS_OPENID_CHECK_CERT']:
            setDefaultFetcher(Urllib2Fetcher())

        @app.route('/_flask_fas_openid_handler/', methods=['GET', 'POST'])
        def flask_fas_openid_handler():
            return self._handle_openid_request()

        app.before_request(self._check_session)

    def _handle_openid_request(self):
        return_url = flask.session['FLASK_FAS_OPENID_RETURN_URL']
        cancel_url = flask.session['FLASK_FAS_OPENID_CANCEL_URL']
        oidconsumer = consumer.Consumer(flask.session, None)
        print 'values: %s' % flask.request.values
        info = oidconsumer.complete(flask.request.values, flask.request.base_url)
        display_identifier = info.getDisplayIdentifier()

        if info.status == consumer.FAILURE and display_identifier:
            return 'FAILURE. display_identifier: %s' % display_identifier
        elif info.status == consumer.CANCEL:
            if cancel_url:
                return flask.redirect(cancel_url)
            return 'OpenID request was cancelled'
        elif info.status == consumer.SUCCESS:
            sreg_resp =  sreg.SRegResponse.fromSuccessResponse(info)
            pape_resp = pape.Response.fromSuccessResponse(info)
            teams_resp = teams.TeamsResponse.fromSuccessResponse(info)
            cla_resp = cla.CLAResponse.fromSuccessResponse(info)
            user = dict()
            user['username'] = sreg_resp.get('nickname')
            user['fullname'] = sreg_resp.get('fullname')
            user['email'] = sreg_resp.get('email')
            user['timezone'] = sreg_resp.get('timezone')
            #user['locale'] = sreg_resp.get('LOCALE')
            user['cla_done'] = cla.CLA_URI_FEDORA_DONE in cla_resp.clas
            user['groups'] = teams_resp.teams
            flask.session['FLASK_FAS_OPENID_USER'] = user
            flask.session.modified = True
            return flask.redirect(return_url)
        else:
            return 'Strange state: %s' % info.status

    def _check_session(self):
        if not 'FLASK_FAS_OPENID_USER' in flask.session or flask.session['FLASK_FAS_OPENID_USER'] is None:
            flask.g.fas_user = None
        else:
            user = flask.session['FLASK_FAS_OPENID_USER']
            # Add approved_memberships to provide backwards compatibility
            # New applications should only use g.fas_user.groups
            user['approved_memberships'] = []
            for group in user['groups']:
                membership = dict()
                membership['name'] = group
                user['approved_memberships'].append(Bunch.fromDict(membership))
            flask.g.fas_user = Bunch.fromDict(user)
        flask.g.fas_session_id = 0

    def login(self, username=None, password=None, return_url=None, cancel_url=None):
        """Tries to log in a user.

        Sets the user information on :attr:`flask.g.fas_user`.
        Will set 0 to :attr:`flask.g.fas_session_id, for compatibility
        with flask_fas.

        :arg username: Not used, but accepted for compatibility with the flask_fas module
        :arg password: Not used, but accepted for compatibility with the flask_fas module
        :arg return_url: The URL to forward the user to after login
        :returns: True if the user was succesfully authenticated.
        :raises: Might raise an redirect to the OpenID endpoint
        """
        if return_url is None:
            if 'next' in args.values:
                return_url = args.values['next']
            else:
                return_url = flask.request.url
        oidconsumer = consumer.Consumer(flask.session, None)
        try:
            request = oidconsumer.begin(self.app.config['FAS_OPENID_ENDPOINT'])
        except consumer.DiscoveryFailure, exc:
            # VERY strange, as this means it could not discover an OpenID endpoint at FAS_OPENID_ENDPOINT
            return 'discoveryfailure'
        if request is None:
            # Also very strange, as this means the discovered OpenID endpoint is no OpenID endpoint
            return 'no-request'
        request.addExtension(sreg.SRegRequest(required=['nickname', 'fullname', 'email', 'timezone']))
        request.addExtension(pape.Request([]))
        request.addExtension(teams.TeamsRequest(requested=['_FAS_ALL_GROUPS_'])) # Magic value which requests all groups from FAS-OpenID >= 0.2.0
        request.addExtension(cla.CLARequest(requested=[cla.CLA_URI_FEDORA_DONE]))
        trust_root = flask.request.url_root
        return_to = flask.request.url_root + '_flask_fas_openid_handler/'
        flask.session['FLASK_FAS_OPENID_RETURN_URL'] = return_url
        flask.session['FLASK_FAS_OPENID_CANCEL_URL'] = cancel_url
        if request.shouldSendRedirect():
            redirect_url = request.redirectURL(trust_root, return_to, False)
            return flask.redirect(redirect_url)
        else:
            return request.htmlMarkup(trust_root, return_to, form_tag_attrs={'id': 'openid_message'}, immediate=False)

    def logout(self):
        '''Logout the user associated with this session
        '''
        flask.session['FLASK_FAS_OPENID_USER'] = None
        flask.g.fas_session_id = None
        flask.g.fas_user = None
        flask.session.modified = True


# This is a decorator we can use with any HTTP method (except login, obviously)
# to require a login.
# If the user is not logged in, it will redirect them to the login form.
# http://flask.pocoo.org/docs/patterns/viewdecorators/#login-required-decorator
def fas_login_required(function):
    """ Flask decorator to ensure that the user is logged in against FAS.
    To use this decorator you need to have a function named 'auth_login'.
    Without that function the redirect if the user is not logged in will not
    work.
    """
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if flask.g.fas_user is None:
            return flask.redirect(flask.url_for('auth_login',
                                                next=flask.request.url))
        return function(*args, **kwargs)
    return decorated_function


def cla_plus_one_required(function):
    """ Flask decorator to retrict access to CLA+1.
    To use this decorator you need to have a function named 'auth_login'.
    Without that function the redirect if the user is not logged in will not
    work.
    """
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if flask.g.fas_user is None or not flask.g.fas_user.cla_done or flask.g.fas_user.groups == []:
            return flask.redirect(flask.url_for('auth_login',
                                                next=flask.request.url))
        else:
            return function(*args, **kwargs)
    return decorated_function
