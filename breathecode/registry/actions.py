import logging, json, os, re, pathlib, base64, hashlib, requests
from typing import Optional
from breathecode.media.models import Media, MediaResolution
from breathecode.media.views import media_gallery_bucket
from breathecode.utils.validation_exception import ValidationException
from django.db.models import Q
from django.contrib.auth.models import User
from django.utils import timezone
from django.template.loader import get_template
from urllib.parse import urlparse
from slugify import slugify
from breathecode.utils import APIException
from breathecode.assessment.models import Assessment
from breathecode.assessment.actions import create_from_asset
from breathecode.authenticate.models import CredentialsGithub
from .models import Asset, AssetTechnology, AssetAlias, AssetErrorLog, ASSET_STATUS, AssetImage
from .serializers import AssetBigSerializer
from .utils import LessonValidator, ExerciseValidator, QuizValidator, AssetException, ProjectValidator, ArticleValidator
from github import Github, GithubException
from breathecode.registry import tasks

logger = logging.getLogger(__name__)

ASSET_STATUS_DICT = [x for x, y in ASSET_STATUS]


def allowed_mimes():
    return ['image/png', 'image/svg+xml', 'image/jpeg', 'image/gif', 'image/jpg']


def asset_images_bucket():
    return os.getenv('ASSET_IMAGES_BUCKET', None)


def generate_external_readme(a):

    if not a.external:
        return False

    readme = get_template('external.md')
    a.set_readme(readme.render(AssetBigSerializer(a).data))
    a.save()
    return True


def get_video_url(video_id):
    if re.search(r'https?:\/\/', video_id) is None:
        return 'https://www.youtube.com/watch?v=' + video_id
    else:
        patterns = ((r'(https?:\/\/www\.loom\.com\/)embed(\/.+)', r'\1share\2'), )
        for regex, replacement in patterns:
            video_id = re.sub(regex, replacement, video_id)
        return video_id


def get_user_from_github_username(username):
    authors = username.split(',')
    github_users = []
    for _author in authors:
        u = CredentialsGithub.objects.filter(username=_author).first()
        if u is not None:
            github_users.append(u.user)
    return github_users


def pull_from_github(asset_slug, author_id=None, override_meta=False):

    logger.debug(f'Sync with github asset {asset_slug}')

    asset = None
    try:

        asset = Asset.objects.filter(slug=asset_slug).first()
        if asset is None:
            raise Exception(f'Asset with slug {asset_slug} not found when attempting to sync with github')

        asset.status_text = 'Starting to sync...'
        asset.sync_status = 'PENDING'
        asset.save()

        if generate_external_readme(asset):
            asset.status_text = 'Readme file for external asset generated, not github sync'
            asset.sync_status = 'OK'
            asset.last_synch_at = None
            asset.save()
            return asset.sync_status

        if asset.owner is not None:
            author_id = asset.owner.id

        if author_id is None:
            raise Exception(
                f'System does not know what github credentials to use to retrieve asset info for: {asset_slug}'
            )

        if asset.readme_url is None or 'github.com' not in asset.readme_url:
            raise Exception(f'Missing or invalid URL on {asset_slug}, it does not belong to github.com')

        credentials = CredentialsGithub.objects.filter(user__id=author_id).first()
        if credentials is None:
            raise Exception(
                f'Github credentials for this user {author_id} not found when sync asset {asset_slug}')

        g = Github(credentials.token)
        if asset.asset_type in ['LESSON', 'ARTICLE']:
            asset = pull_github_lesson(g, asset, override_meta=override_meta)
        elif asset.asset_type in ['QUIZ']:
            asset = pull_quiz_asset(g, asset)
        else:
            asset = pull_learnpack_asset(g, asset, override_meta=override_meta)

        asset.status_text = 'Successfully Synched'
        asset.sync_status = 'OK'
        asset.last_synch_at = timezone.now()
        asset.save()
        logger.debug(f'Successfully re-synched asset {asset_slug} with github')

        return asset
    except Exception as e:
        raise e
        message = ''
        if hasattr(e, 'data'):
            message = e.data['message']
        else:
            message = str(e).replace('"', '\'')

        logger.error(f'Error updating {asset_slug} from github: ' + str(message))
        # if the exception triggered too early, the asset will be early
        if asset is not None:
            asset.status_text = str(message)
            asset.sync_status = 'ERROR'
            asset.save()
            return asset.sync_status

    return 'ERROR'


def push_to_github(asset_slug, author=None):

    logger.debug(f'Push asset {asset_slug} to github')

    asset = None
    try:

        asset = Asset.objects.filter(slug=asset_slug).first()
        if asset is None:
            raise Exception(f'Asset with slug {asset_slug} not found when attempting github push')

        asset.status_text = 'Starting to push...'
        asset.sync_status = 'PENDING'
        asset.save()

        if author is None:
            author = asset.owner

        if asset.external:
            raise Exception('Asset is marked as "external" so it cannot push to github')

        if author is None:
            raise Exception('Asset must have an owner with write permissions on the repository')

        if asset.readme_url is None or 'github.com' not in asset.readme_url:
            raise Exception(f'Missing or invalid URL on {asset_slug}, it does not belong to github.com')

        credentials = CredentialsGithub.objects.filter(user__id=author.id).first()
        if credentials is None:
            raise Exception(
                f'Github credentials for user {author.first_name} {author.last_name} (id: {author.id}) not found when synching asset {asset_slug}'
            )

        g = Github(credentials.token)
        asset = push_github_asset(g, asset)

        asset.status_text = 'Successfully Synched'
        asset.sync_status = 'OK'
        asset.last_synch_at = timezone.now()
        asset.save()
        logger.debug(f'Successfully re-synched asset {asset_slug} with github')

        return asset
    except Exception as e:
        # raise e
        message = ''
        if hasattr(e, 'data'):
            message = e.data['message']
        else:
            message = str(e).replace('"', '\'')

        logger.error(f'Error updating {asset_slug} from github: ' + str(message))
        # if the exception triggered too early, the asset will be early
        if asset is not None:
            asset.status_text = str(message)
            asset.sync_status = 'ERROR'
            asset.save()
            return asset.sync_status

    return 'ERROR'


def get_url_info(url: str):

    result = re.search(r'blob\/([\w\-]+)', url)
    branch_name = None
    if result is not None:
        branch_name = result.group(1)

    result = re.search(r'https?:\/\/github\.com\/([\w\-]+)\/([\w\-]+)\/?', url)
    if result is None:
        raise Exception('Invalid URL when looking organization: ' + url)

    org_name = result.group(1)
    repo_name = result.group(2)

    return org_name, repo_name, branch_name


def get_blob_content(repo, path_name, branch='main'):
    # first get the branch reference
    ref = repo.get_git_ref(f'heads/{branch}')
    # then get the tree
    tree = repo.get_git_tree(ref.object.sha, recursive='/' in path_name).tree
    # look for path in tree
    sha = [x.sha for x in tree if x.path == path_name]
    if not sha:
        # well, not found..
        return None
    # we have sha
    return repo.get_git_blob(sha[0])


def set_blob_content(repo, path_name, content, branch='main'):

    if content is None or content == '':
        raise Exception(f'Blob content is empty for {path_name}')

    # first get the branch reference
    ref = repo.get_git_ref(f'heads/{branch}')
    # then get the tree
    tree = repo.get_git_tree(ref.object.sha, recursive='/' in path_name).tree
    # look for path in tree
    file = [x for x in tree if x.path == path_name]
    if not file:
        # well, not found..
        return None

    # update
    return repo.update_file(file[0].path, 'Updated from admin.4Geeks.com', content, file[0].sha)


def push_github_asset(github, asset):

    logger.debug(f'Sync pull_github_lesson {asset.slug}')

    if asset.readme_url is None:
        raise Exception('Missing Readme URL for asset ' + asset.slug + '.')

    org_name, repo_name, branch_name = get_url_info(asset.readme_url)
    repo = github.get_repo(f'{org_name}/{repo_name}')

    file_name = os.path.basename(asset.readme_url)

    if branch_name is None:
        raise Exception('Readme URL must include branch name after blob')

    result = re.search(r'\/blob\/([\w\d_\-]+)\/(.+)', asset.readme_url)
    branch, file_path = result.groups()
    logger.debug(f'Fetching readme: {file_path}')

    decoded_readme = base64.b64decode(asset.readme.encode('utf-8')).decode('utf-8')
    set_blob_content(repo, file_path, decoded_readme, branch=branch)

    return asset


def pull_github_lesson(github, asset, override_meta=False):

    logger.debug(f'Sync pull_github_lesson {asset.slug}')

    if asset.readme_url is None:
        raise Exception('Missing Readme URL for lesson ' + asset.slug + '.')

    org_name, repo_name, branch_name = get_url_info(asset.readme_url)
    repo = github.get_repo(f'{org_name}/{repo_name}')

    file_name = os.path.basename(asset.readme_url)

    if branch_name is None:
        raise Exception('Lesson URL must include branch name after blob')

    result = re.search(r'\/blob\/([\w\d_\-]+)\/(.+)', asset.readme_url)
    branch, file_path = result.groups()
    logger.debug(f'Fetching readme: {file_path}')

    base64_readme = get_blob_content(repo, file_path, branch=branch_name).content
    asset.readme_raw = base64_readme

    # only the first time a lesson is synched it will override some of the properties
    readme = asset.get_readme(parse=True)
    if asset.last_synch_at is None or override_meta:
        fm = dict(readme['frontmatter'].items())
        if 'slug' in fm and fm['slug'] != asset.slug:
            logger.debug(f'New slug {fm["slug"]} found for lesson {asset.slug}')
            asset.slug = fm['slug']

        if 'excerpt' in fm:
            asset.description = fm['excerpt']
        elif 'subtitle' in fm:
            asset.description = fm['subtitle']

        if 'title' in fm and fm['title'] != '':
            asset.title = fm['title']

        if 'authors' in fm and fm['authors'] != '':
            asset.authors_username = ','.join(fm['authors'])

        if 'tags' in fm and isinstance(fm['tags'], list):
            asset.technologies.clear()
            for tech_slug in fm['tags']:
                technology = AssetTechnology.get_or_create(tech_slug)
                asset.technologies.add(technology)

    return asset


def clean_asset_readme(asset):
    if asset.readme_raw is None or asset.readme_raw == '':
        return asset

    asset.last_cleaning_at = timezone.now()
    try:
        asset = clean_readme_relative_paths(asset)
        asset = clean_readme_hide_comments(asset)
        asset = clean_h1s(asset)
        readme = asset.get_readme(parse=True)
        if 'html' in readme:
            asset.html = readme['html']

        asset.cleaning_status = 'OK'
        asset.save()
    except Exception as e:
        asset.cleaning_status = 'ERROR'
        asset.cleaning_status_details = str(e)
        asset.save()

    return asset


def clean_readme_relative_paths(asset):
    readme = asset.get_readme()
    base_url = os.path.dirname(asset.readme_url)
    relative_urls = list(re.finditer(r'((?:\.\.?\/)+[^)"\']+)', readme['decoded']))

    replaced = readme['decoded']
    while len(relative_urls) > 0:
        match = relative_urls.pop(0)
        found_url = match.group()
        if found_url.endswith('\\'):
            found_url = found_url[:-1].strip()
        extension = pathlib.Path(found_url).suffix
        if readme['decoded'][match.start() - 1] in ['(', "'", '"'] and extension and extension.strip() in [
                '.png', '.jpg', '.png', '.jpeg', '.svg', '.gif'
        ]:
            logger.debug('Replaced url: ' + base_url + '/' + found_url + '?raw=true')
            replaced = replaced.replace(found_url, base_url + '/' + found_url + '?raw=true')

    asset.set_readme(replaced)
    return asset


def clean_readme_hide_comments(asset):
    logger.debug(f'Clearning readme for asset {asset.slug}')
    readme = asset.get_readme()
    regex = r'<!--\s+(:?end)?hide\s+-->'

    content = readme['decoded']
    findings = list(re.finditer(regex, content))

    if len(findings) % 2 != 0:
        asset.log_error(AssetErrorLog.README_SYNTAX, 'Readme with to many <!-- hide -> comments')
        raise Exception('Readme with to many <!-- hide -> comments')

    replaced = ''
    startIndex = 0
    while len(findings) > 1:
        opening_comment = findings.pop(0)
        endIndex = opening_comment.start()

        replaced += content[startIndex:endIndex]

        closing_comment = findings.pop(0)
        startIndex = closing_comment.end()

    replaced += content[startIndex:]
    asset.set_readme(replaced)
    return asset


def clean_h1s(asset):
    logger.debug(f'Clearning first heading 1 for {asset.slug}')
    readme = asset.get_readme()
    content = readme['decoded'].strip()

    end = r'.*\n'
    lines = list(re.finditer(end, content))
    if len(lines) == 0:
        logger.debug('no jump of lines found')
        return asset

    first_line_end = lines.pop(0).end()
    logger.debug('first line ends at')
    logger.debug(first_line_end)

    regex = r'\s?#\s[`\-_\w]+[`\-_\w\s]*\n'
    findings = list(re.finditer(regex, content[:first_line_end]))
    if len(findings) > 0:
        replaced = content[first_line_end:].strip()
        asset.set_readme(replaced)

    return asset


def screenshots_bucket():
    return os.getenv('SCREENSHOTS_BUCKET', '')


class AssetThumbnailGenerator:
    asset: Asset
    width: Optional[int]
    height: Optional[int]

    def __init__(self, asset: Asset, width: Optional[int] = 0, height: Optional[int] = 0) -> None:
        self.asset = asset
        self.width = width
        self.height = height

    def get_thumbnail_url(self) -> tuple[str, bool]:
        """
        Get thumbnail url for asset, the first element of tuple is the url, the second if is permanent
        redirect.
        """
        if not self.asset:
            return (self._get_default_url(), False)
        media = self._get_media()
        if not media:
            tasks.async_create_asset_thumbnail.delay(self.asset.slug)
            return (self._get_asset_url(), False)

        if not self._the_client_want_resize():
            # register click
            media.hits += 1
            media.save()

            if self.asset.preview is None or self.asset.preview == '':
                self.asset.preview = media.url
                self.asset.save()

            return (media.url, True)

        media_resolution = self._get_media_resolution(media.hash)
        if not media_resolution:
            # register click
            media.hits += 1
            media.save()

            tasks.async_resize_asset_thumbnail.delay(media.id, width=self.width, height=self.height)
            return (media.url, False)

        # register click
        media_resolution.hits += 1
        media_resolution.save()

        if self.asset.preview is None or self.asset.preview == '':
            self.asset.preview = media.url
            self.asset.save()

        return (f'{media.url}-{media_resolution.width}x{media_resolution.height}', True)

    def _get_default_url(self) -> str:
        return os.getenv('DEFAULT_ASSET_PREVIEW_URL', '')

    def _get_asset_url(self) -> str:
        return (self.asset and self.asset.preview) or self._get_default_url()

    def _get_media(self) -> Optional[Media]:
        if not self.asset:
            return None

        slug = self.asset.get_thumbnail_name().split('.')[0]
        return Media.objects.filter(slug=slug).first()

    def _get_media_resolution(self, hash: str) -> Optional[MediaResolution]:
        return MediaResolution.objects.filter(Q(width=self.width) | Q(height=self.height), hash=hash).first()

    def _the_client_want_resize(self) -> bool:
        """
        Check if the width of height value was provided, if both are provided return False
        """

        return bool((self.width and not self.height) or (not self.width and self.height))


def pull_learnpack_asset(github, asset, override_meta):

    if asset.readme_url is None:
        raise Exception('Missing Readme URL for asset ' + asset.slug + '.')

    org_name, repo_name, branch_name = get_url_info(asset.readme_url)
    repo = github.get_repo(f'{org_name}/{repo_name}')

    lang = asset.lang
    if lang is None or lang == '':
        raise Exception('Language for this asset is not defined, impossible to retrieve readme')
    elif lang in ['us', 'en']:
        lang = ''
    else:
        lang = '.' + lang

    readme_file = None
    try:
        readme_file = repo.get_contents(f'README{lang}.md')
    except:
        raise Exception(f'Translation on README{lang}.md not found')

    learn_file = None
    try:
        learn_file = repo.get_contents('learn.json')
    except:
        try:
            learn_file = repo.get_contents('.learn/learn.json')
        except:
            try:
                learn_file = repo.get_contents('bc.json')
            except:
                try:
                    learn_file = repo.get_contents('.learn/bc.json')
                except:
                    raise Exception('No configuration learn.json or bc.json file was found')

    base64_readme = str(readme_file.content)
    asset.readme_raw = base64_readme

    if learn_file is not None and (asset.last_synch_at is None or override_meta):
        config = json.loads(learn_file.decoded_content.decode('utf-8'))
        asset.config = config

        # only replace title and description of English language
        if 'title' in config and (lang == '' or asset.title == '' or asset.title is None):
            asset.title = config['title']
        if 'description' in config and (lang == '' or asset.description == '' or asset.description is None):
            asset.description = config['description']

        if 'preview' in config:
            asset.preview = config['preview']
        else:
            raise Exception(f'Missing preview URL')

        if 'video-id' in config:
            asset.solution_video_url = get_video_url(str(config['video-id']))
            asset.with_video = True

        if 'duration' in config:
            asset.duration = config['duration']
        if 'difficulty' in config:
            asset.difficulty = config['difficulty'].upper()
        if 'solution' in config:
            asset.solution = config['solution']
            asset.with_solutions = True

        if 'technologies' in config:
            asset.technologies.clear()
            for tech_slug in config['technologies']:
                technology = AssetTechnology.get_or_create(tech_slug)
                asset.technologies.add(technology)

        if 'delivery' in config:
            if 'instructions' in config['delivery']:
                if isinstance(config['delivery']['instructions'], str):
                    asset.delivery_instructions = config['delivery']['instructions']
                elif isinstance(config['delivery']['instructions'],
                                dict) and asset.lang in config['delivery']['instructions']:
                    asset.delivery_instructions = config['delivery']['instructions'][asset.lang]

            if 'formats' in config['delivery']:
                if isinstance(config['delivery']['formats'], list):
                    asset.delivery_formats = ','.join(config['delivery']['formats'])
                elif isinstance(config['delivery']['formats'], str):
                    asset.delivery_formats = config['delivery']['formats']

            if 'url' in asset.delivery_formats:
                if 'regex' in config['delivery'] and isinstance(config['delivery']['regex'], str):
                    asset.delivery_regex_url = config['delivery']['regex'].replace('\\\\', '\\')

    return asset


def pull_quiz_asset(github, asset):

    logger.debug(f'Sync pull_quiz_asset {asset.slug}')

    if asset.readme_url is None:
        raise Exception('Missing Readme URL for quiz ' + asset.slug + '.')

    org_name, repo_name, branch_name = get_url_info(asset.readme_url)
    repo = github.get_repo(f'{org_name}/{repo_name}')

    file_name = os.path.basename(asset.readme_url)

    if branch_name is None:
        raise Exception('Quiz URL must include branch name after blob')

    result = re.search(r'\/blob\/([\w\d_\-]+)\/(.+)', asset.readme_url)
    branch, file_path = result.groups()
    logger.debug(f'Fetching quiz json: {file_path}')

    encoded_config = get_blob_content(repo, file_path, branch=branch_name).content
    decoded_config = Asset.decode(encoded_config)
    asset.config = json.loads(decoded_config)
    asset.save()

    asset = create_from_asset(asset)
    return asset


def test_asset(asset):
    try:
        validator = None
        if asset.asset_type == 'LESSON':
            validator = LessonValidator(asset)
        elif asset.asset_type == 'EXERCISE':
            validator = ExerciseValidator(asset)
        elif asset.asset_type == 'PROJECT':
            validator = ProjectValidator(asset)
        elif asset.asset_type == 'QUIZ':
            validator = QuizValidator(asset)
        elif asset.asset_type == 'ARTICLE':
            validator = ArticleValidator(asset)

        validator.validate()
        asset.status_text = 'Test Successfull'
        asset.test_status = 'OK'
        asset.last_test_at = timezone.now()
        asset.save()
        return True
    except AssetException as e:
        asset.status_text = str(e)
        asset.test_status = e.severity
        asset.last_test_at = timezone.now()
        asset.save()
        # raise e
        return False
    except Exception as e:
        asset.status_text = str(e)
        asset.test_status = 'ERROR'
        asset.last_test_at = timezone.now()
        asset.save()
        # raise e
        return False


def upload_image_to_bucket(img, asset):

    from ..services.google_cloud import Storage

    link = img.original_url
    if 'github.com' in link and not 'raw=true' in link:
        if '?' in link:
            link = link + '&raw=true'
        else:
            link = link + '?raw=true'

    r = requests.get(link, stream=True, timeout=2)
    if r.status_code != 200:
        raise Exception(f'Error downloading image from asset {asset.slug}: {link}')

    found_mime = [mime for mime in allowed_mimes() if r.headers['content-type'] in mime]
    if len(found_mime) == 0:
        raise Exception(
            f"Skipping image download for {link} in asset {asset.slug}, invalid mime {r.headers['content-type']}"
        )

    img.hash = hashlib.sha256(r.content).hexdigest()

    storage = Storage()

    extension = pathlib.Path(img.name).suffix
    cloud_file = storage.file(asset_images_bucket(), img.hash + extension)
    if not cloud_file.exists():
        cloud_file.upload(r.content)

    img.hash = img.hash
    img.mime = found_mime[0]
    img.bucket_url = cloud_file.url()
    img.download_status = 'OK'
    img.save()

    img.assets.add(asset)

    return img
