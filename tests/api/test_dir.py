import json
import os
from mock import patch

from django.core.urlresolvers import reverse

from seahub.test_utils import BaseTestCase

try:
    from seahub.settings import LOCAL_PRO_DEV_ENV
except ImportError:
    LOCAL_PRO_DEV_ENV = False

class DirTest(BaseTestCase):
    def setUp(self):
        self.endpoint = reverse('DirView', args=[self.repo.id])
        self.folder_name = os.path.basename(self.folder)

    def tearDown(self):
        self.remove_repo()

    def test_can_list(self):
        self.login_as(self.user)
        resp = self.client.get(self.endpoint)
        json_resp = json.loads(resp.content)

        self.assertEqual(200, resp.status_code)
        assert len(json_resp) == 1
        assert self.folder_name == json_resp[0]['name']

    def test_can_create(self):
        self.login_as(self.user)
        resp = self.client.post(self.endpoint + '?p=/new_dir', {
            'operation': 'mkdir'
        })

        json_resp = json.loads(resp.content)
        self.assertEqual(201, resp.status_code)
        assert 'dir' == json_resp['type']

    def test_can_create_with_reload_dir(self):
        self.login_as(self.user)
        resp = self.client.post(self.endpoint + '?p=/new_dir&reloaddir=true', {
            'operation': 'mkdir'
        })

        json_resp = json.loads(resp.content)
        self.assertEqual(200, resp.status_code)
        assert 'new_dir' in (json_resp[0]['name'], json_resp[1]['name'])

    def test_invalid_repo(self):
        self.login_as(self.user)
        repo_id = self.repo.id
        invalid_repo_id = repo_id[:30] + '123456'

        invalid_endpoint = reverse('DirView', args=[invalid_repo_id])
        resp = self.client.post(invalid_endpoint + '?p=/new_dir', {
            'operation': 'mkdir'
        })

        self.assertEqual(404, resp.status_code)

    def test_invalid_repo_permission(self):
        self.login_as(self.admin)
        repo_id = self.repo.id
        endpoint = reverse('DirView', args=[repo_id])
        resp = self.client.post(endpoint + '?p=/new_dir', {
            'operation': 'mkdir'
        })

        self.assertEqual(403, resp.status_code)

    def test_invalid_parent_dir(self):
        self.login_as(self.user)
        resp = self.client.post(self.endpoint + '?p=/invalid_parent_dir/new_dir', {
            'operation': 'mkdir',
        })

        self.assertEqual(400, resp.status_code)

    @patch('seahub.api2.views.is_seafile_pro')
    def test_create_parents(self, mock_is_seafile_pro):

        if not LOCAL_PRO_DEV_ENV:
            return

        mock_is_seafile_pro.return_value = True

        self.login_as(self.user)
        resp = self.client.post(self.endpoint + '?p=/not_exist_parent_dir/new_dir', {
            'operation': 'mkdir',
            'create_parents': 'true',
        })

        self.assertEqual(201, resp.status_code)

    @patch('seahub.api2.views.is_seafile_pro')
    def test_can_not_create_parents_if_not_pro(self, mock_is_seafile_pro):
        mock_is_seafile_pro.return_value = False

        self.login_as(self.user)
        resp = self.client.post(self.endpoint + '?p=/not_exist_parent_dir/new_dir', {
            'operation': 'mkdir',
            'create_parents': 'true',
        })

        self.assertEqual(400, resp.status_code)
