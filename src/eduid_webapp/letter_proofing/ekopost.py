# -*- encoding: utf-8 -*-

from __future__ import absolute_import

import json
import base64
from datetime import datetime
from hammock import Hammock

__author__ = 'john'


class EkopostException(Exception):
    pass


class Ekopost(object):

    _ekopost_api = None

    def __init__(self, app):
        self.app = app

    @property
    def ekopost_api(self):
        if self._ekopost_api is None:
            verify_ssl = True
            auth = None
            if self.app.config.get("EKOPOST_API_VERIFY_SSL", None) == 'false':
                verify_ssl = False
            if self.app.config.get("EKOPOST_API_USER", None) and self.app.config.get("EKOPOST_API_PW"):
                auth = (self.app.config.get("EKOPOST_API_USER"), self.app.config.get("EKOPOST_API_PW"))
            self._ekopost_api = Hammock(self.app.config.get("EKOPOST_API_URI"), auth=auth, verify=verify_ssl)
        return self._ekopost_api

    def send(self, eppn, document):
        """
        Send a letter containing a PDF-document
        to the recipient specified in the document.

        :param eppn: eduPersonPrincipalName
        :param document: PDF-document to be sent
        """

        # Output date is set it to current time since
        # we want to send the letter as soon as possible.
        outpute_date = datetime.utcnow().__str__()

        # An easily identifiable name for the campaign and envelope
        letter_id = eppn + "+" + outpute_date

        original_document_size = document.len
        document_in_base64 = base64.b64encode(document.getvalue())

        # Create a campaign and the envelope that it should contain
        campaign = self._create_campaign(letter_id, outpute_date, 'eduID')
        envelope = self._create_envelope(campaign['id'], letter_id)

        # Include the PDF-document to send
        self._create_content(campaign['id'], envelope['id'],
                             document_in_base64, original_document_size)

        # To mark the letter as ready to be printed and sent:
        # 1. Close the envelope belonging to the campaign.
        # 2. Close the campaign that holds the envelope.
        self._close_evenlope(campaign['id'], envelope['id'])
        closed_campaign = self._close_campaign(campaign['id'])

        return closed_campaign['id']

    def _create_campaign(self, name, output_date, cost_center):
        """
        Create a new campaign

        :param name: A name to identify the campaign.
        :param output_date: Date in UTC when the envelope
                            should be printed and distributed.
        :param cost_center: The internal cost center.
        """
        campaign_data = json.dumps({
            'name': name,
            'output_date': output_date,
            'cost_center': cost_center
        })

        response = self.ekopost_api.campaigns.POST(data=campaign_data, headers={'Content-Type': 'application/json'})

        if response.status_code == 200:
            return response.json()

        raise EkopostException('Ekopost exception: {!s} {!s}'.format(response.status_code, response.text))

    def _create_envelope(self, campaign_id, name, postage='priority', plex='simplex', color='false'):
        """
        Create an envelope for a specified campaign

        :param campaign_id: Id of the campaign within which to create the envelope
        :param name: A name to identify the envelope
        :param postage: Send with 'priority' to deliver within one working
                        day after printing, or send with 'economy' to deliver
                        within four working days after printing.
        :param plex: Use 'simplex' to print on one page or 'duplex' to print
                     on both front and back.
        :param color: Print in color (CMYK) or set to false for black and white.
        """
        envelope_data = json.dumps({
            'name': name,
            'postage': postage,
            'plex': plex,
            'color': color
        })

        response = self.ekopost_api.\
            campaigns(campaign_id).\
            envelopes.POST(
            data=envelope_data,
            headers={'Content-Type': 'application/json'})

        if response.status_code == 200:
            return response.json()

        raise EkopostException('Ekopost exception: {!s} {!s}'.format(response.status_code, response.text))

    def _create_content(self, campaign_id, envelope_id, data, length, mime='application/pdf', type='document'):
        """
        Create the content that should be linked to an envelope

        :param campaign_id: Unique id of a campaign within which the envelope exists
        :param envelope_id: Unique id of an envelope to add the content to
        :param data: The document as a base64 encoded string
        :param length: The document's size in bytes
        :param mime: The document's mime type
        :param type: Content type, which can be either 'document' or 'attachment'
        """
        content_data = json.dumps({
            'campaign_id': campaign_id,
            'envelope_id': envelope_id,
            'data': data,
            'mime': mime,
            'length': length,
            'type': type
        })

        response = self.ekopost_api.\
            campaigns(campaign_id).\
            envelopes(envelope_id).\
            content.POST(
            data=content_data,
            headers={'Content-Type': 'application/json'})

        if response.status_code == 200:
            return response.json()

        raise EkopostException('Ekopost exception: {!s} {!s}'.format(response.status_code, response.text))

    def _close_evenlope(self, campaign_id, envelope_id):
        """
        Change an envelope state to closed and mark it as ready for print & distribution.
        :param campaign_id: Unique id of a campaign within which the envelope exists
        :param envelope_id: Unique id of the envelope that should be closed
        """
        response = self.ekopost_api.\
            campaigns(campaign_id).\
            envelopes(envelope_id).\
            close.POST(headers={'Content-Type': 'application/json'})

        if response.status_code == 200:
            return response.json()

        raise EkopostException('Ekopost exception: {!s} {!s}'.format(response.status_code, response.text))

    def _close_campaign(self, campaign_id):
        """
        Change a campains state to closed and mark it and all its
        envelopes as ready for print & distribution.

        :param campaign_id: Unique id of a campaign that should be closed
        """
        response = self.ekopost_api.\
            campaigns(campaign_id).\
            close.POST(headers={'Content-Type': 'application/json'})

        if response.status_code == 200:
            return response.json()

        raise EkopostException('Ekopost exception: {!s} {!s}'.format(response.status_code, response.text))
