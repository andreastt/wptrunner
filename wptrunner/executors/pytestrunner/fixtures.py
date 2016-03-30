# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest


class Session(object):
    def __init__(self, client):
        self.client = client

    @pytest.fixture(scope="module")
    def session(self, request):
        request.addfinalizer(self.client.end)
        return self.client
