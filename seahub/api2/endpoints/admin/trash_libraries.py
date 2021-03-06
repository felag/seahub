import logging

from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from seaserv import seafile_api
from pysearpc import SearpcError

from seahub.utils import is_valid_username
from seahub.utils.timeutils import timestamp_to_isoformat_timestr

from seahub.api2.authentication import TokenAuthentication
from seahub.api2.throttling import UserRateThrottle
from seahub.api2.utils import api_error

logger = logging.getLogger(__name__)


class AdminTrashLibraries(APIView):

    authentication_classes = (TokenAuthentication, SessionAuthentication)
    throttle_classes = (UserRateThrottle,)
    permission_classes = (IsAdminUser,)

    def get(self, request, format=None):
        """ List deleted repos (by owner)

        Permission checking:
        1. only admin can perform this action.
        """
        search_owner = request.GET.get('owner', '')
        if search_owner:
            if not is_valid_username(search_owner):
                error_msg = 'owner invalid.'
                return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

            repos = seafile_api.get_trash_repos_by_owner(search_owner)
        else:
            repos = seafile_api.get_trash_repo_list(-1, -1)

        return_repos = []
        for repo in repos:
            result = {}
            result['name'] = repo.repo_name
            result['id'] = repo.repo_id
            result['owner'] = repo.owner_id
            result['delete_time'] = timestamp_to_isoformat_timestr(repo.del_time)

            return_repos.append(result)

        return Response({"search_owner": search_owner, "repos": return_repos})

    def delete(self, request, format=None):
        """ clean all deleted libraries(by owner)

        Permission checking:
        1. only admin can perform this action.
        """

        owner = request.data.get('owner', '')
        try:
            if owner:
                if not is_valid_username(owner):
                    error_msg = 'owner invalid.'
                    return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

                seafile_api.empty_repo_trash_by_owner(owner)
            else:
                seafile_api.empty_repo_trash()
        except SearpcError as e:
            logger.error(e)
            error_msg = 'Internal Server Error'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        return Response({'success': True})

class AdminTrashLibrary(APIView):

    authentication_classes = (TokenAuthentication, SessionAuthentication)
    throttle_classes = (UserRateThrottle,)
    permission_classes = (IsAdminUser,)

    def put(self, request, repo_id, format=None):
        """ restore a deleted library

        Permission checking:
        1. only admin can perform this action.
        """

        try:
            seafile_api.restore_repo_from_trash(repo_id)
        except SearpcError as e:
            logger.error(e)
            error_msg = 'Internal Server Error'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        return Response({'success': True})

    def delete(self, request, repo_id, format=None):
        """ permanently delete a deleted library

        Permission checking:
        1. only admin can perform this action.
        """

        try:
            seafile_api.del_repo_from_trash(repo_id)
        except SearpcError as e:
            logger.error(e)
            error_msg = 'Internal Server Error'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        return Response({'success': True})
