# encoding: utf-8
import os
import logging
import json
from dateutil.relativedelta import relativedelta
from constance import config

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect, Http404, \
    HttpResponseBadRequest
from django.utils.translation import ugettext as _
from django.contrib import messages
from django.utils import timezone
from django.utils.html import escape
import seaserv
from seaserv import seafile_api
from seaserv import ccnet_threaded_rpc, is_org_group, \
    get_org_id_by_group, del_org_group_repo, unset_inner_pub_repo
from pysearpc import SearpcError

from seahub.share.forms import FileLinkShareForm, \
    UploadLinkShareForm
from seahub.share.models import FileShare, UploadLinkShare, OrgFileShare
from seahub.share.signals import share_repo_to_user_successful
from seahub.auth.decorators import login_required, login_required_ajax
from seahub.base.decorators import require_POST
from seahub.contacts.signals import mail_sended
from seahub.views import is_registered_user, check_folder_permission
from seahub.utils import string2list, gen_shared_link, \
    gen_shared_upload_link, IS_EMAIL_CONFIGURED, check_filename_with_rename, \
    is_valid_username, is_valid_email, send_html_email, is_org_context, \
    gen_token, normalize_cache_key
from seahub.utils.mail import send_html_email_with_dj_template, MAIL_PRIORITY
from seahub.settings import SITE_ROOT, REPLACE_FROM_EMAIL, ADD_REPLY_TO_HEADER
from seahub.profile.models import Profile

# Get an instance of a logger
logger = logging.getLogger(__name__)

########## rpc wrapper    
def is_org_repo_owner(username, repo_id):
    owner = seaserv.seafserv_threaded_rpc.get_org_repo_owner(repo_id)
    return True if owner == username else False

def get_org_group_repos_by_owner(org_id, username):
    return seaserv.seafserv_threaded_rpc.get_org_group_repos_by_owner(org_id,
                                                                      username)

def list_org_inner_pub_repos_by_owner(org_id, username):
    return seaserv.seafserv_threaded_rpc.list_org_inner_pub_repos_by_owner(
        org_id, username)

def org_share_repo(org_id, repo_id, from_user, to_user, permission):
    return seaserv.seafserv_threaded_rpc.org_add_share(org_id, repo_id,
                                                       from_user, to_user,
                                                       permission)

def org_remove_share(org_id, repo_id, from_user, to_user):
    return seaserv.seafserv_threaded_rpc.org_remove_share(org_id, repo_id,
                                                          from_user, to_user)

########## functions
def share_to_group(request, repo, group, permission):
    """Share repo to group with given permission.
    """
    repo_id = repo.id
    group_id = group.id
    from_user = request.user.username

    if is_org_context(request):
        org_id = request.user.org.org_id
        group_repo_ids = seafile_api.get_org_group_repoids(org_id, group.id)
    else:
        group_repo_ids = seafile_api.get_group_repoids(group.id)

    if repo.id in group_repo_ids:
        return False

    try:
        if is_org_context(request):
            org_id = request.user.org.org_id
            seafile_api.add_org_group_repo(repo_id, org_id, group_id,
                                           from_user, permission)
        else:
            seafile_api.set_group_repo(repo_id, group_id, from_user,
                                       permission)
        return True
    except Exception, e:
        logger.error(e)
        return False

def share_to_user(request, repo, to_user, permission):
    """Share repo to a user with given permission.
    """
    repo_id = repo.id
    from_user = request.user.username

    if from_user == to_user:
        return False

    # permission check
    if is_org_context(request):
        org_id = request.user.org.org_id
        if not seaserv.ccnet_threaded_rpc.org_user_exists(org_id, to_user):
            return False
    else:
        if not is_registered_user(to_user):
            return False

    try:
        if is_org_context(request):
            org_id = request.user.org.org_id
            org_share_repo(org_id, repo_id, from_user, to_user, permission)
        else:
            seafile_api.share_repo(repo_id, from_user, to_user, permission)
    except SearpcError as e:
            return False
            logger.error(e)
    else:
        # send a signal when sharing repo successful
        share_repo_to_user_successful.send(sender=None,
                                           from_user=from_user,
                                           to_user=to_user, repo=repo)
        return True


########## views
@login_required_ajax
@require_POST
def ajax_repo_remove_share(request):
    """
    Remove repo shared to user/group/public
    """
    content_type = 'application/json; charset=utf-8'

    repo_id = request.POST.get('repo_id', None)
    share_type = request.POST.get('share_type', None)

    if not seafile_api.get_repo(repo_id):
        return HttpResponse(json.dumps({'error': _(u'Library does not exist')}), status=400,
                            content_type=content_type)

    username = request.user.username

    if share_type == 'personal':

        from_email = request.POST.get('from', None)
        if not is_valid_username(from_email):
            return HttpResponse(json.dumps({'error': _(u'Invalid argument')}), status=400,
                                content_type=content_type)

        if is_org_context(request):
            org_id = request.user.org.org_id
            org_remove_share(org_id, repo_id, from_email, username)
        else:
            seaserv.remove_share(repo_id, from_email, username)
        return HttpResponse(json.dumps({'success': True}), status=200,
                            content_type=content_type)

    elif share_type == 'group':

        from_email = request.POST.get('from', None)
        if not is_valid_username(from_email):
            return HttpResponse(json.dumps({'error': _(u'Invalid argument')}), status=400,
                                content_type=content_type)

        group_id = request.POST.get('group_id', None)
        group = seaserv.get_group(group_id)
        if not group:
            return HttpResponse(json.dumps({'error': _(u"Group does not exist")}), status=400,
                                content_type=content_type)

        if seaserv.check_group_staff(group_id, username) or \
            seafile_api.is_repo_owner(username, repo_id):
            if is_org_group(group_id):
                org_id = get_org_id_by_group(group_id)
                del_org_group_repo(repo_id, org_id, group_id)
            else:
                seafile_api.unset_group_repo(repo_id, group_id, from_email)
            return HttpResponse(json.dumps({'success': True}), status=200,
                                content_type=content_type)
        else:
            return HttpResponse(json.dumps({'error': _(u'Permission denied')}), status=400,
                                content_type=content_type)

    elif share_type == 'public':

        if is_org_context(request):

            org_repo_owner = seafile_api.get_org_repo_owner(repo_id)
            is_org_repo_owner = True if org_repo_owner == username else False
            if request.user.org.is_staff or is_org_repo_owner:
                org_id = request.user.org.org_id
                seaserv.seafserv_threaded_rpc.unset_org_inner_pub_repo(org_id,
                                                                       repo_id)
                return HttpResponse(json.dumps({'success': True}), status=200,
                                    content_type=content_type)
            else:
                return HttpResponse(json.dumps({'error': _(u'Permission denied')}), status=403,
                                    content_type=content_type)

        else:
            if seafile_api.is_repo_owner(username, repo_id) or \
                request.user.is_staff:
                unset_inner_pub_repo(repo_id)
                return HttpResponse(json.dumps({'success': True}), status=200,
                                    content_type=content_type)
            else:
                return HttpResponse(json.dumps({'error': _(u'Permission denied')}), status=403,
                                    content_type=content_type)
    else:
        return HttpResponse(json.dumps({'error': _(u'Invalid argument')}), status=400,
                            content_type=content_type)

def get_share_out_repo_list(request):
    """List repos that @user share to other users.

    Returns:
        A list of repos.
    """
    username = request.user.username
    if is_org_context(request):
        org_id = request.user.org.org_id
        return seafile_api.get_org_share_out_repo_list(org_id, username,
                                                       -1, -1)
    else:
        return seafile_api.get_share_out_repo_list(username, -1, -1)

def get_group_repos_by_owner(request):
    """List repos that @user share to groups.

    Returns:
        A list of repos.
    """
    username = request.user.username
    if is_org_context(request):
        org_id = request.user.org.org_id
        return get_org_group_repos_by_owner(org_id, username)
    else:
        return seaserv.get_group_repos_by_owner(username)

def list_inner_pub_repos_by_owner(request):
    """List repos that @user share to organizatoin.

    Returns:
        A list of repos, or empty list if in cloud_mode.
    """
    username = request.user.username
    if is_org_context(request):
        org_id = request.user.org.org_id
        return list_org_inner_pub_repos_by_owner(org_id, username)
    elif request.cloud_mode:
        return []
    else:
        return seaserv.list_inner_pub_repos_by_owner(username)

def list_share_out_repos(request):
    shared_repos = []

    # repos shared from this user
    shared_repos += get_share_out_repo_list(request)

    # repos shared to groups
    group_repos = get_group_repos_by_owner(request)
    for repo in group_repos:
        group = ccnet_threaded_rpc.get_group(int(repo.group_id))
        if not group:
            repo.props.user = ''
            continue
        repo.props.user = group.props.group_name
        repo.props.user_info = repo.group_id
    shared_repos += group_repos

    # inner pub repos
    pub_repos = list_inner_pub_repos_by_owner(request)
    for repo in pub_repos:
        repo.props.user = _(u'all members')
        repo.props.user_info = 'all'
    shared_repos += pub_repos

    return shared_repos

########## share link
@login_required_ajax
def send_shared_link(request):
    """
    Handle ajax post request to send file shared link.
    """
    if not request.method == 'POST':
        raise Http404

    content_type = 'application/json; charset=utf-8'

    if not IS_EMAIL_CONFIGURED:
        data = json.dumps({'error':_(u'Sending shared link failed. Email service is not properly configured, please contact administrator.')})
        return HttpResponse(data, status=500, content_type=content_type)

    from seahub.settings import SITE_NAME

    form = FileLinkShareForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data['email']
        file_shared_link = form.cleaned_data['file_shared_link']
        file_shared_name = form.cleaned_data['file_shared_name']
        file_shared_type = form.cleaned_data['file_shared_type']
        extra_msg = escape(form.cleaned_data['extra_msg'])

        to_email_list = string2list(email)
        send_success, send_failed = [], []
        # use contact_email, if present
        username = Profile.objects.get_contact_email_by_user(request.user.username)
        for to_email in to_email_list:
            if not is_valid_email(to_email):
                send_failed.append(to_email)
                continue

            # Add email to contacts.
            mail_sended.send(sender=None, user=request.user.username,
                             email=to_email)

            c = {
                'email': request.user.username,
                'to_email': to_email,
                'file_shared_link': file_shared_link,
                'file_shared_name': file_shared_name,
            }

            if extra_msg:
                c['extra_msg'] = extra_msg

            if REPLACE_FROM_EMAIL:
                from_email = username
            else:
                from_email = None  # use default from email

            if ADD_REPLY_TO_HEADER:
                reply_to = username
            else:
                reply_to = None

            try:
                if file_shared_type == 'f':
                    c['file_shared_type'] = _(u"file")
                    send_html_email(_(u'A file is shared to you on %s') % SITE_NAME,
                                    'shared_link_email.html',
                                    c, from_email, [to_email],
                                    reply_to=reply_to
                                    )
                else:
                    c['file_shared_type'] = _(u"directory")
                    send_html_email(_(u'A directory is shared to you on %s') % SITE_NAME,
                                    'shared_link_email.html',
                                    c, from_email, [to_email],
                                    reply_to=reply_to)

                send_success.append(to_email)
            except Exception:
                send_failed.append(to_email)

        if len(send_success) > 0:
            data = json.dumps({"send_success": send_success, "send_failed": send_failed})
            return HttpResponse(data, status=200, content_type=content_type)
        else:
            data = json.dumps({"error": _("Internal server error, or please check the email(s) you entered")})
            return HttpResponse(data, status=400, content_type=content_type)
    else:
        return HttpResponseBadRequest(json.dumps(form.errors),
                                      content_type=content_type)

@login_required
def save_shared_link(request):
    """Save public share link to one's library.
    """
    username = request.user.username
    token = request.GET.get('t', '')
    dst_repo_id = request.POST.get('dst_repo', '')
    dst_path = request.POST.get('dst_path', '')

    next = request.META.get('HTTP_REFERER', None)
    if not next:
        next = SITE_ROOT

    if not dst_repo_id or not dst_path:
        messages.error(request, _(u'Please choose a directory.'))
        return HttpResponseRedirect(next)

    if check_folder_permission(request, dst_repo_id, dst_path) != 'rw':
        messages.error(request, _('Permission denied'))
        return HttpResponseRedirect(next)

    try:
        fs = FileShare.objects.get(token=token)
    except FileShare.DoesNotExist:
        raise Http404

    src_repo_id = fs.repo_id
    src_path = os.path.dirname(fs.path)
    obj_name = os.path.basename(fs.path)

    new_obj_name = check_filename_with_rename(dst_repo_id, dst_path, obj_name)

    seafile_api.copy_file(src_repo_id, src_path, obj_name,
                          dst_repo_id, dst_path, new_obj_name, username,
                          need_progress=0)

    messages.success(request, _(u'Successfully saved.'))
    return HttpResponseRedirect(next)

@login_required_ajax
def send_shared_upload_link(request):
    """
    Handle ajax post request to send shared upload link.
    """
    if not request.method == 'POST':
        raise Http404

    content_type = 'application/json; charset=utf-8'

    if not IS_EMAIL_CONFIGURED:
        data = json.dumps({'error':_(u'Sending shared upload link failed. Email service is not properly configured, please contact administrator.')})
        return HttpResponse(data, status=500, content_type=content_type)

    from seahub.settings import SITE_NAME

    form = UploadLinkShareForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data['email']
        shared_upload_link = form.cleaned_data['shared_upload_link']
        extra_msg = escape(form.cleaned_data['extra_msg'])

        to_email_list = string2list(email)
        send_success, send_failed = [], []
        # use contact_email, if present
        username = Profile.objects.get_contact_email_by_user(request.user.username)
        for to_email in to_email_list:
            if not is_valid_email(to_email):
                send_failed.append(to_email)
                continue
            # Add email to contacts.
            mail_sended.send(sender=None, user=request.user.username,
                             email=to_email)

            c = {
                'email': request.user.username,
                'to_email': to_email,
                'shared_upload_link': shared_upload_link,
                }

            if extra_msg:
                c['extra_msg'] = extra_msg

            if REPLACE_FROM_EMAIL:
                from_email = username
            else:
                from_email = None  # use default from email

            if ADD_REPLY_TO_HEADER:
                reply_to = username
            else:
                reply_to = None

            try:
                send_html_email(_(u'An upload link is shared to you on %s') % SITE_NAME,
                                'shared_upload_link_email.html',
                                c, from_email, [to_email],
                                reply_to=reply_to)

                send_success.append(to_email)
            except Exception:
                send_failed.append(to_email)

        if len(send_success) > 0:
            data = json.dumps({"send_success": send_success, "send_failed": send_failed})
            return HttpResponse(data, status=200, content_type=content_type)
        else:
            data = json.dumps({"error": _("Internal server error, or please check the email(s) you entered")})
            return HttpResponse(data, status=400, content_type=content_type)
    else:
        return HttpResponseBadRequest(json.dumps(form.errors),
                                      content_type=content_type)
@login_required_ajax
def ajax_get_upload_link(request):
    content_type = 'application/json; charset=utf-8'

    if request.method == 'GET':
        repo_id = request.GET.get('repo_id', None)
        path = request.GET.get('p', None)

        # augument check
        if not repo_id:
            data = json.dumps({'error': 'repo_id invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if not path:
            data = json.dumps({'error': 'p invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        # resource check
        try:
            repo = seafile_api.get_repo(repo_id)
        except Exception as e:
            logger.error(e)
            data = json.dumps({'error': 'Internal Server Error'})
            return HttpResponse(data, status=500, content_type=content_type)

        if not repo:
            data = json.dumps({'error': 'Library %s not found.' % repo_id})
            return HttpResponse(data, status=404, content_type=content_type)

        if not path.endswith('/'):
            path = path + '/'

        if not seafile_api.get_dir_id_by_path(repo_id, path):
            data = json.dumps({'error': 'Folder %s not found.' % path})
            return HttpResponse(data, status=404, content_type=content_type)

        # permission check
        if not check_folder_permission(request, repo_id, path):
            data = json.dumps({'error': 'Permission denied.'})
            return HttpResponse(data, status=403, content_type=content_type)

        # get upload link
        username = request.user.username
        l = UploadLinkShare.objects.filter(repo_id=repo_id).filter(
            username=username).filter(path=path)

        data = {}
        if len(l) > 0:
            token = l[0].token
            data['upload_link'] = gen_shared_upload_link(token)
            data['token'] = token

        return HttpResponse(json.dumps(data), content_type=content_type)

    elif request.method == 'POST':
        repo_id = request.POST.get('repo_id', None)
        path = request.POST.get('p', None)
        use_passwd = True if int(request.POST.get('use_passwd', '0')) == 1 else False
        passwd = request.POST.get('passwd') if use_passwd else None

        # augument check
        if not repo_id:
            data = json.dumps({'error': 'repo_id invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if not path:
            data = json.dumps({'error': 'p invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if passwd and len(passwd) < config.SHARE_LINK_PASSWORD_MIN_LENGTH:
            data = json.dumps({'error': _('Password is too short')})
            return HttpResponse(data, status=400, content_type=content_type)

        # resource check
        try:
            repo = seafile_api.get_repo(repo_id)
        except Exception as e:
            logger.error(e)
            data = json.dumps({'error': 'Internal Server Error'})
            return HttpResponse(data, status=500, content_type=content_type)

        if not repo:
            data = json.dumps({'error': 'Library %s not found.' % repo_id})
            return HttpResponse(data, status=404, content_type=content_type)

        if not path.endswith('/'):
            path = path + '/'

        if not seafile_api.get_dir_id_by_path(repo_id, path):
            data = json.dumps({'error': 'Folder %s not found.' % path})
            return HttpResponse(data, status=404, content_type=content_type)

        # permission check
        # normal permission check & default/guest user permission check
        if check_folder_permission(request, repo_id, path) != 'rw' or \
            not request.user.permissions.can_generate_shared_link():
            data = json.dumps({'error': 'Permission denied.'})
            return HttpResponse(data, status=403, content_type=content_type)

        # generate upload link
        l = UploadLinkShare.objects.filter(repo_id=repo_id).filter(
            username=request.user.username).filter(path=path)

        if len(l) > 0:
            # if already exist
            upload_link = l[0]
            token = upload_link.token
        else:
            # generate new
            username = request.user.username
            uls = UploadLinkShare.objects.create_upload_link_share(
                username, repo_id, path, passwd)
            token = uls.token

        shared_upload_link = gen_shared_upload_link(token)
        data = json.dumps({'token': token, 'upload_link': shared_upload_link})

        return HttpResponse(data, content_type=content_type)

@login_required_ajax
def ajax_get_download_link(request):
    """
    Handle ajax request to generate file or dir shared link.
    """
    content_type = 'application/json; charset=utf-8'

    if request.method == 'GET':
        repo_id = request.GET.get('repo_id', None)
        share_type = request.GET.get('type', 'f')
        path = request.GET.get('p', None)

        # augument check
        if not repo_id:
            data = json.dumps({'error': 'repo_id invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if not path:
            data = json.dumps({'error': 'p invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if share_type not in ('f', 'd'):
            data = json.dumps({'error': 'type invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        # resource check
        try:
            repo = seafile_api.get_repo(repo_id)
        except Exception as e:
            logger.error(e)
            data = json.dumps({'error': 'Internal Server Error'})
            return HttpResponse(data, status=500, content_type=content_type)

        if not repo:
            data = json.dumps({'error': 'Library %s not found.' % repo_id})
            return HttpResponse(data, status=404, content_type=content_type)

        if share_type == 'f':
            if not seafile_api.get_file_id_by_path(repo_id, path):
                data = json.dumps({'error': 'File %s not found.' % path})
                return HttpResponse(data, status=404, content_type=content_type)

        if share_type == 'd':
            if not path.endswith('/'):
                path = path + '/'

            if not seafile_api.get_dir_id_by_path(repo_id, path):
                data = json.dumps({'error': 'Folder %s not found.' % path})
                return HttpResponse(data, status=404, content_type=content_type)

        # permission check
        if not check_folder_permission(request, repo_id, path):
            data = json.dumps({'error': 'Permission denied.'})
            return HttpResponse(data, status=403, content_type=content_type)

        # get download link
        username = request.user.username
        l = FileShare.objects.filter(repo_id=repo_id).filter(
            username=username).filter(path=path)

        data = {}
        if len(l) > 0:
            token = l[0].token
            data['download_link'] = gen_shared_link(token, l[0].s_type)
            data['token'] = token
            data['is_expired'] = l[0].is_expired()

        return HttpResponse(json.dumps(data), content_type=content_type)

    elif request.method == 'POST':
        repo_id = request.POST.get('repo_id', None)
        path = request.POST.get('p', None)
        share_type = request.POST.get('type', 'f')
        use_passwd = True if int(request.POST.get('use_passwd', '0')) == 1 else False
        passwd = request.POST.get('passwd') if use_passwd else None

        # augument check
        if not repo_id:
            data = json.dumps({'error': 'repo_id invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if not path:
            data = json.dumps({'error': 'p invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if share_type not in ('f', 'd'):
            data = json.dumps({'error': 'type invalid.'})
            return HttpResponse(data, status=400, content_type=content_type)

        if passwd and len(passwd) < config.SHARE_LINK_PASSWORD_MIN_LENGTH:
            data = json.dumps({'error': _('Password is too short')})
            return HttpResponse(data, status=400, content_type=content_type)

        try:
            expire_days = int(request.POST.get('expire_days', 0))
        except ValueError:
            expire_days = 0

        if expire_days <= 0:
            expire_date = None
        else:
            expire_date = timezone.now() + relativedelta(days=expire_days)

        # resource check
        try:
            repo = seafile_api.get_repo(repo_id)
        except Exception as e:
            logger.error(e)
            data = json.dumps({'error': 'Internal Server Error'})
            return HttpResponse(data, status=500, content_type=content_type)

        if not repo:
            data = json.dumps({'error': 'Library %s not found.' % repo_id})
            return HttpResponse(data, status=404, content_type=content_type)

        if share_type == 'f':
            if not seafile_api.get_file_id_by_path(repo_id, path):
                data = json.dumps({'error': 'File %s not found.' % path})
                return HttpResponse(data, status=404, content_type=content_type)

        if share_type == 'd':
            if not path.endswith('/'):
                path = path + '/'

            if not seafile_api.get_dir_id_by_path(repo_id, path):
                data = json.dumps({'error': 'Folder %s not found.' % path})
                return HttpResponse(data, status=404, content_type=content_type)

        # permission check
        # normal permission check & default/guest user permission check
        if check_folder_permission(request, repo_id, path) != 'rw' or \
            not request.user.permissions.can_generate_shared_link():
            data = json.dumps({'error': 'Permission denied.'})
            return HttpResponse(data, status=403, content_type=content_type)

        username = request.user.username
        if share_type == 'f':
            fs = FileShare.objects.get_file_link_by_path(username, repo_id, path)
            if fs is None:
                fs = FileShare.objects.create_file_link(username, repo_id, path,
                                                        passwd, expire_date)
                if is_org_context(request):
                    org_id = request.user.org.org_id
                    OrgFileShare.objects.set_org_file_share(org_id, fs)
        else:
            fs = FileShare.objects.get_dir_link_by_path(username, repo_id, path)
            if fs is None:
                fs = FileShare.objects.create_dir_link(username, repo_id, path,
                                                       passwd, expire_date)
                if is_org_context(request):
                    org_id = request.user.org.org_id
                    OrgFileShare.objects.set_org_file_share(org_id, fs)

        token = fs.token
        shared_link = gen_shared_link(token, fs.s_type)
        data = json.dumps({'token': token, 'download_link': shared_link})
        return HttpResponse(data, content_type=content_type)

@login_required_ajax
@require_POST
def ajax_private_share_dir(request):
    content_type = 'application/json; charset=utf-8'

    repo_id = request.POST.get('repo_id', '')
    path = request.POST.get('path', '')
    username = request.user.username
    result = {}

    repo = seafile_api.get_repo(repo_id)
    if not repo:
        result['error'] = _(u'Library does not exist.')
        return HttpResponse(json.dumps(result), status=400, content_type=content_type)

    if seafile_api.get_dir_id_by_path(repo_id, path) is None:
        result['error'] = _(u'Directory does not exist.')
        return HttpResponse(json.dumps(result), status=400, content_type=content_type)

    if path != '/':
        # if share a dir, check sub-repo first
        try:
            if is_org_context(request):
                org_id = request.user.org.org_id
                sub_repo = seaserv.seafserv_threaded_rpc.get_org_virtual_repo(
                    org_id, repo_id, path, username)
            else:
                sub_repo = seafile_api.get_virtual_repo(repo_id, path, username)
        except SearpcError as e:
            result['error'] = e.msg
            return HttpResponse(json.dumps(result), status=500, content_type=content_type)

        if not sub_repo:
            name = os.path.basename(path)
            # create a sub-lib
            try:
                # use name as 'repo_name' & 'repo_desc' for sub_repo
                if is_org_context(request):
                    org_id = request.user.org.org_id
                    sub_repo_id = seaserv.seafserv_threaded_rpc.create_org_virtual_repo(
                        org_id, repo_id, path, name, name, username)
                else:
                    sub_repo_id = seafile_api.create_virtual_repo(repo_id, path,
                        name, name, username)
                sub_repo = seafile_api.get_repo(sub_repo_id)
            except SearpcError as e:
                result['error'] = e.msg
                return HttpResponse(json.dumps(result), status=500, content_type=content_type)

        shared_repo_id = sub_repo.id
        shared_repo = sub_repo
    else:
        shared_repo_id = repo_id
        shared_repo = repo

    emails_string = request.POST.get('emails', '')
    groups_string = request.POST.get('groups', '')
    perm = request.POST.get('perm', '')

    emails = string2list(emails_string)
    groups = string2list(groups_string)

    # Test whether user is the repo owner.
    if not seafile_api.is_repo_owner(username, shared_repo_id) and \
            not is_org_repo_owner(username, shared_repo_id):
        result['error'] = _(u'Only the owner of the library has permission to share it.')
        return HttpResponse(json.dumps(result), status=500, content_type=content_type)

    # Parsing input values.
    # no 'share_to_all'
    share_to_groups, share_to_users, shared_success, shared_failed = [], [], [], []

    for email in emails:
        email = email.lower()
        if is_valid_username(email):
            share_to_users.append(email)
        else:
            shared_failed.append(email)

    for group_id in groups:
        share_to_groups.append(seaserv.get_group(group_id))

    for email in share_to_users:
        # Add email to contacts.
        mail_sended.send(sender=None, user=request.user.username, email=email)
        if share_to_user(request, shared_repo, email, perm):
            shared_success.append(email)
        else:
            shared_failed.append(email)

    for group in share_to_groups:
        if share_to_group(request, shared_repo, group, perm):
            shared_success.append(group.group_name)
        else:
            shared_failed.append(group.group_name)

    if len(shared_success) > 0:
        return HttpResponse(json.dumps({
            "shared_success": shared_success,
            "shared_failed": shared_failed
            }), content_type=content_type)
    else:
        # for case: only share to users and the emails are not valid
        data = json.dumps({"error": _("Please check the email(s) you entered")})
        return HttpResponse(data, status=400, content_type=content_type)

def ajax_get_link_audit_code(request):
    """
    Generate a token, and record that token with email in cache, expires in
    one hour, send token to that email address.

    User provide token and email at share link page, if the token and email
    are valid, record that email in session.
    """
    content_type = 'application/json; charset=utf-8'

    token = request.POST.get('token')
    email = request.POST.get('email')
    if not is_valid_email(email):
        return HttpResponse(json.dumps({
            'error': _('Email address is not valid')
        }), status=400, content_type=content_type)

    dfs = FileShare.objects.get_valid_file_link_by_token(token)
    ufs = UploadLinkShare.objects.get_valid_upload_link_by_token(token)

    fs = dfs if dfs else ufs
    if fs is None:
        return HttpResponse(json.dumps({
            'error': _('Share link is not found')
        }), status=400, content_type=content_type)

    cache_key = normalize_cache_key(email, 'share_link_audit_')
    timeout = 60 * 60           # one hour
    code = gen_token(max_length=6)
    cache.set(cache_key, code, timeout)

    # send code to user via email
    subject = _("Verification code for visiting share links")
    c = {
        'code': code,
    }
    try:
        send_html_email_with_dj_template(
            email, dj_template='share/audit_code_email.html',
            context=c, subject=subject, priority=MAIL_PRIORITY.now
        )
        return HttpResponse(json.dumps({'success': True}), status=200,
                            content_type=content_type)
    except Exception as e:
        logger.error('Failed to send audit code via email to %s')
        logger.error(e)
        return HttpResponse(json.dumps({
            "error": _("Failed to send a verification code, please try again later.")
        }), status=500, content_type=content_type)
