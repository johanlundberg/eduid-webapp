# -*- coding: utf-8 -*-

from __future__ import absolute_import

from eduid_common.api.app import eduid_init_app

__author__ = 'lundberg'


def proofing_consumer_init_app(name, config):
    """
    Create an instance of an eduid support app.

    First, it will load the configuration from support.settings.common
    then any settings given in the `config` param.

    Then, the app instance will be updated with common stuff by `eduid_init_app`,
    and finally all needed blueprints will be registered with it.

    :param name: The name of the instance, it will affect the configuration loaded.
    :type name: str
    :param config: any additional configuration settings. Specially useful
                   in test cases
    :type config: dict

    :return: the flask app
    :rtype: flask.Flask
    """

    app = eduid_init_app(name, config)

    app.logger.info('Init {} app...'.format(name))
    return app
