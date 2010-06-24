# -*- coding: utf-8 -*-
"""
String Level models.
"""
import datetime, hashlib, sys
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import permalink
from django.db import models
from django.utils.translation import ugettext_lazy as _

from languages.models import Language
from projects.models import Project
from txcommon.log import logger

def reset():
    Translation.objects.all().delete()
    StringSet.objects.all().delete()
    SourceEntity.objects.all().delete()

# State Codes for translations
TRANSLATION_STATE_CHOICES = (
    ('APR', 'Approved'),
    ('FUZ', 'Fuzzy'),
    ('REJ', 'Rejected'),
)

from django.db import transaction

"""
Parsers need to be somewhat rewritten, currently each one implements parse(buf) function which returns libtransifex.core.StringSet class,
and compile(stringset) which returns file buffer.

It actually makes more sense to store all uploaded files, parse only the information we are interested in, and during compilation,
take the uploaded file as template, and just replace modified parts
"""

from libtransifex.qt import LinguistParser # Qt4 TS files
from libtransifex.java import JavaPropertiesParser # Java .properties
from libtransifex.apple import AppleStringsParser # Apple .strings
#from libtransifex.ruby import YamlParser # Ruby On Rails (broken)
#from libtransifex.resx import ResXmlParser # Microsoft .NET (not finished)
from libtransifex.pofile import PofileParser # GNU Gettext .PO/.POT parser

PARSERS = [PofileParser, LinguistParser, JavaPropertiesParser, AppleStringsParser]

# For faster lookup
PARSER_MAPPING = {}
for parser in PARSERS:
    PARSER_MAPPING[parser.mime_type] = parser


class ResourceGroup(models.Model):
    """
    Model for grouping Resources.
    """
    # FIXME: add necessary fields
    pass


class ResourceManager(models.Manager):
    pass

class Resource(models.Model):
    """
    A translation resource, equivalent to a POT file, YAML file, string stream etc.
    
    The Resource points to a source language (template) file path! For example,
    it should be pointing to a .pot file or to a english .po file.of a project
    with english as the source language.
    The path_to_file should be point to :
        1. the relative path of the pot/source file in the vcs folder hierarchy
        2. an absolute URL (not local) to the file.
    The path_to_file should be used for loading (pull) operations!
    """

    name = models.CharField(_('Name'), max_length=255, null=False,
        blank=False, 
        help_text=_('A descriptive name unique inside the project.'))

    # Short identifier to be used in the API URLs
    slug = models.SlugField(_('Slug'), max_length=50,
        help_text=_('A short label to be used in the URL, containing only '
            'letters, numbers, underscores or hyphens.'))

    # Timestamps
    created = models.DateTimeField(auto_now_add=True, editable=False)
    last_update = models.DateTimeField(auto_now=True, editable=False)

    # Foreign Keys
    source_language = models.ForeignKey(Language,
        verbose_name=_('Source Language'),blank=False, null=False,
        help_text=_("The source language of this Resource."))

    project = models.ForeignKey(Project, verbose_name=_('Project'),
        blank=False,
        null=True,
        help_text=_("The project which owns the translation resource."))

    resource_group = models.ForeignKey(ResourceGroup, verbose_name=_("Resource Group"),
        blank=True, null=True,
        help_text=_("A group under which Resources are organized."))

    # Managers
    objects = ResourceManager()

    def __unicode__(self):
        return self.name

    class Meta:
        unique_together = (('name', 'project'),
                           ('slug', 'project'),)
        verbose_name = _('resource')
        verbose_name_plural = _('resources')
        ordering  = ['name',]
        order_with_respect_to = 'project'
        get_latest_by = 'created'

    @permalink
    def get_absolute_url(self):
        return ('project.resource', None, { 'project_slug': self.project.slug, 'resource_slug' : self.slug })

    @property
    def source_strings(self):
        """
        Return the list of all the strings, belonging to the Source Language
        of the Project/Resource.
        
        CAUTION! This function returns Translation and not SourceEntity objects!
        """
        return Translation.objects.filter(resource = self,
                                          language = self.source_language)

    # TODO: Invalidation for cached data!!!
    @property
    def wordcount(self):
        """
        Return the number of words which need translation in this resource.
        
        The counting of the words uses the Translation objects of the SOURCE
        LANGUAGE as set of objects.
        """
        cache_key = ('wordcount.%s.%s' % (self.project.slug, self.slug))
        wc = cache.get(cache_key)
        if not wc:
            wc = 0
            for ss in self.source_strings:
                wc += ss.wordcount
        return wc

    @property
    def last_committer(self):
        """
        Return the overall last committer for the translation of this resource.
        """
        lt = self.last_translation(language=None)
        if lt:
            return lt.user
        return None

    def last_translation(self, language=None):
        """
        Return the last translation for this Resource and the specific lang.
        
        If None language value then return in all languages avaible the last 
        translation.
        """
        if language:
            target_language = Language.objects.by_code_or_alias(language)
            t = Translation.objects.filter(resource=self,
                    language=target_language).order_by('-last_update')
        else:
            t = Translation.objects.filter(resource=self).order_by('-last_update')
        if t:
            return t[0]
        return None

    @property
    def available_languages(self):
        """
        Return the languages with at least one Translation of a SourceEntity for
        this Resource.
        """
        languages = Translation.objects.filter(resource=self).values_list(
            'language', flat=True).distinct()
        return Language.objects.filter(id__in=languages).distinct()

    def translated_strings(self, language):
        """
        Return the QuerySet of source entities, translated in this language.
        
        This assumes that we DO NOT SAVE empty strings for untranslated entities!
        """
        target_language = Language.objects.by_code_or_alias(language)
        return SourceEntity.objects.filter(resource=self,
            id__in=Translation.objects.filter(language=target_language,
                resource=self).values_list('source_entity', flat=True))

    def untranslated_strings(self, language):
        """
        Return the QuerySet of source entities which are not yet translated in
        the specific language.
        
        This assumes that we DO NOT SAVE empty strings for untranslated entities!
        """
        target_language = Language.objects.by_code_or_alias(language)
        return SourceEntity.objects.filter(resource=self).exclude(
            id__in=Translation.objects.filter(language=target_language,
                resource=self).values_list('source_entity', flat=True))

    def num_translated(self, language):
        """
        Return the number of translated strings in this Resource for the language.
        """
        return self.translated_strings(language).count()

    def num_untranslated(self, language):
        """
        Return the number of untranslated strings in this Resource for the language.
        """
        return self.untranslated_strings(language).count()

    #TODO:We need this as a cached value in order to avoid hitting the db all the time
    @property
    def total_entities(self):
        """Return the total number of source entities to be translated."""
        return SourceEntity.objects.filter(resource=self).count()

    @property
    def total_source_strings(self):
        """
        It is the same functionality with the 'total_entities' property but
        here we use the Translation objects to calculate the total strings which
        are being translated.
        """
        return Translation.objects.filter(resource = self,
                                          language = self.source_language).count()

    def trans_percent(self, language):
        """Return the percent of untranslated strings in this Resource."""
        t = self.num_translated(language)
        try:
            return (t * 100 / self.total_entities)
        except ZeroDivisionError:
            return 100

    def untrans_percent(self, language):
        """Return the percent of untranslated strings in this Resource."""
        translated_percent = self.trans_percent(language)
        return (100 - translated_percent)
        # With the next approach we lose some data because we cut floating points
#        u = self.num_untranslated(language)
#        try:
#            return (u * 100 / self.total_entities)
#        except ZeroDivisionError:
#            return 0

    @transaction.commit_manually
    def merge_stringset(self, stringset, target_language, user=None, overwrite_translations=True):
        try:
            strings_added = 0
            strings_updated = 0
            for j in stringset.strings:
                # If is primary language update source strings!
                se, created = SourceEntity.objects.get_or_create(
                    string = j.source_entity,
                    context = j.context or "None",
                    resource = self,
                    number = j.number,
                    defaults = {
                        'position' : 1,
                        }
                    )
                tr, created = Translation.objects.get_or_create(
                    source_entity = se,
                    language = target_language,
                    resource = self,
                    number = j.number,
                    defaults = {
                        'string' : j.translation,
                        'user' : user,
                        },
                    )

                if created:
                    strings_added += 1

                if not created and overwrite_translations:
                    if ts.string != j.translation:
                        ts.string = j.translation
                        strings_updated += 1
                        updated = True
        except:
            transaction.rollback()
            return 0,0
        else:
            transaction.commit()
            return strings_added, strings_updated

    def merge_translation_file(self, translation_file):
        stringset = PARSER_MAPPING[translation_file.mime_type].parse_file(filename = translation_file.get_storage_path())
        return self.merge_stringset(stringset, translation_file.language)

class SourceEntity(models.Model):
    """
    A representation of a source string which is translated in many languages.
    
    The SourceEntity is pointing to a specific Resource and it is uniquely 
    defined by the string, context and resource fields (so they are unique
    together).
    """
    string = models.CharField(_('String'), max_length=255,
        blank=False, null=False,
        help_text=_("The actual string content of source string."))
    context = models.CharField(_('Context'), max_length=255,
        blank=False, null=False,
        help_text=_("A description of the source string. This field specifies"
                    "the context of the source string inside the resource."))
    position = models.IntegerField(_('Position'), blank=True, null=True,
        help_text=_("The position of the source string in the Resource."
                    "For example, the specific position of msgid field in a "
                    "po template (.pot) file in gettext."))
    #TODO: Decision for the following
    occurrences = models.TextField(_('Occurrences'), max_length=1000,
        blank=True, editable=False, null=True,
        help_text=_("The occurrences of the source string in the project code."))
    flags = models.TextField(_('Flags'), max_length=100,
        blank=True, editable=False,
        help_text=_("The flags which mark the source string. For example, if"
                    "there is a python formatted string this is marked as "
                    "\"#, python-format\" in gettext."))
    developer_comment = models.TextField(_('Flags'), max_length=1000,
        blank=True, editable=False,
        help_text=_("The comment of the developer."))

    number = models.IntegerField(_('Number'), blank=False,
         null=False, default=0,
        help_text=_("The number of the string. 0 for singular and 1, 2, 3, "
                    "etc. for plural forms."))

    # Timestamps
    created = models.DateTimeField(auto_now_add=True, editable=False)
    last_update = models.DateTimeField(auto_now=True, editable=False)

    # Foreign Keys
    # A source string must always belong to a resource
    resource = models.ForeignKey(Resource, verbose_name=_('Resource'),
        blank=False, null=False,
        help_text=_("The translation resource which owns the source string."))

    singular = models.ForeignKey('SourceEntity', verbose_name=_('Singular'),
        blank=True, null=True,
        help_text=_("The source entity that is the singular reference for"
            " this plural source entity. If this source entity is not a"
            " plural one, leave it as blank."))

    def __unicode__(self):
        return self.string

    class Meta:
        unique_together = (('string', 'context', 'resource', 'number'),)
        verbose_name = _('source string')
        verbose_name_plural = _('source strings')
        ordering = ['string', 'context']
        order_with_respect_to = 'resource'
        get_latest_by = 'created'


class SearchStringManager(models.Manager):
    def by_source_entity_and_language(self, string,
            source_code='en', target_code=None):
        """
        Return the results of searching, based on a specific source string and
        maybe on specific source and/or target language.
        """
        source_entitys = []

        source_entitys = SourceEntity.objects.filter(string=string,)

        # If no target language given search on any target language.
        if target_code:
            language = Language.objects.by_code_or_alias(target_code)
            results = self.filter(
                        source_entity__in=source_entitys, language=language)
        else:
            results = self.filter(source_entity__in=source_entitys)
        return results


class Translation(models.Model):
    """
    The representation of a live translation for a given source string.
    
    This model encapsulates all the necessary fields for the translation of a 
    source string in a specific target language. It also contains a set of meta
    fields for the context of this translation.
    """

    string = models.CharField(_('String'), max_length=255,
        blank=False, null=False,
        help_text=_("The actual string content of translation."))

    number = models.IntegerField(_('Number'), blank=False,
         null=False, default=0,
        help_text=_("The number of the string. 0 for singular and 1, 2, 3, "
                    "etc. for plural forms."))

    # Timestamps
    created = models.DateTimeField(auto_now_add=True, editable=False)
    last_update = models.DateTimeField(auto_now=True, editable=False)

    # Foreign Keys
    # A source string must always belong to a resource
    source_entity = models.ForeignKey(SourceEntity,
        verbose_name=_('Source String'),
        blank=False, null=False,
        help_text=_("The source string which is being translated by this"
                    "translation string instance."))

    language = models.ForeignKey(Language,
        verbose_name=_('Target Language'),blank=False, null=True,
        help_text=_("The language in which this translation string belongs to."))

    # Foreign Keys
    # A source string must always belong to a resource
    resource = models.ForeignKey(Resource, verbose_name=_('Resource'),
        blank=False, null=False,
        help_text=_("The translation resource which owns the source string."))

    user = models.ForeignKey(User,
        verbose_name=_('Committer'), blank=False, null=True,
        help_text=_("The user who committed the specific translation."))

    #TODO: Managers
    objects = SearchStringManager()

    def __unicode__(self):
        return self.string

    class Meta:
        unique_together = (('source_entity', 'string', 'language', 'resource',
                            'number'),)
        verbose_name = _('translation string')
        verbose_name_plural = _('translation strings')
        ordering  = ['string',]
        order_with_respect_to = 'source_entity'
        get_latest_by = 'created'

    # TODO: needs caching
    @property
    def wordcount(self):
        """
        Return the number of words for this translation string.
        """
        # use None to split at any whitespace regardless of length
        # so for instance double space counts as one space
        return len(self.string.split(None))

class TranslationSuggestion(models.Model):
    """
    A suggestion for the translation of a specific source string in a language.

    Suggestions are used as hints to the committers of the original translations.
    A fuzzy translation string is also put here as a suggestion. Suggestions
    can also be used (if it is chosen) to give non-team members the chance
    to suggest a translation on a source string, permitting anonymous or
    arbitrary logged in user translation.
    Suggestions have a score which can be increased or decreased by users,
    indicating how good is the translation of the source string.
    The best translation could be automatically chosen as a live 
    Translation by using a heuristic.
    """

    string = models.CharField(_('String'), max_length=255,
        blank=False, null=False,
        help_text=_("The actual string content of translation."))
    score = models.FloatField(_('Score'), blank=True, null=True, default=0,
        help_text=_("A value which indicates the relevance of this translation."))
    live = models.BooleanField(_('Live'), default=False, editable=False)

    # Timestamps
    created = models.DateTimeField(auto_now_add=True, editable=False)
    last_update = models.DateTimeField(auto_now=True, editable=False)

    # Foreign Keys
    # A source string must always belong to a resource
    source_entity = models.ForeignKey(SourceEntity,
        verbose_name=_('Source String'),
        blank=False, null=False,
        help_text=_("The source string which is being translated by this"
                    "suggestion instance."))
    language = models.ForeignKey(Language,
        verbose_name=_('Target Language'),blank=False, null=True,
        help_text=_("The language in which this translation string belongs to."))
    user = models.ForeignKey(User,
        verbose_name=_('Committer'), blank=False, null=True,
        help_text=_("The user who committed the specific suggestion."))

    #TODO: Managers

    def __unicode__(self):
        return self.string

    class Meta:
        # Only one suggestion can be committed by each user for a source_entity 
        # in a specific language!
        unique_together = (('source_entity', 'string', 'language'),)
        verbose_name = _('translation suggestion')
        verbose_name_plural = _('translation suggestions')
        ordering  = ['string',]
        order_with_respect_to = 'source_entity'
        get_latest_by = 'created'

class StorageFile(models.Model):
    """
    StorageFile refers to a uploaded file. Initially
    """
    # File name of the uploaded file
    name = models.CharField(max_length=1024)
    size = models.IntegerField(_('File size in bytes'), blank=True, null=True)
    mime_type = models.CharField(max_length=255)

    # Path for storage
    uuid = models.CharField(max_length=1024)

    # Foreign Keys
    language = models.ForeignKey(Language,
        verbose_name=_('Source language'),blank=False, null=True,
        help_text=_("The language in which this translation string belongs to."))

    #resource = models.ForeignKey(Resource, verbose_name=_('Resource'),
        #blank=False, null=True,
        #help_text=_("The translation resource which owns the source string."))

#    project = models.ForeignKey(Project, verbose_name=_('Project'), blank=False, null=True)

    bound = models.BooleanField(verbose_name=_('Bound to any object'), default=False,
        help_text=_('Wether this file is bound to a project/translation resource, otherwise show in the upload box'))

    user = models.ForeignKey(User,
        verbose_name=_('Owner'), blank=False, null=True,
        help_text=_("The user who uploaded the specific file."))
    
    created = models.DateTimeField(auto_now_add=True, editable=False)
    total_strings = models.IntegerField(_('Total number of strings'), blank=True, null=True)

    def __unicode__(self):
        return "%s (%s)" % (self.name, self.uuid)

    def get_storage_path(self):
        return "/tmp/%s-%s" % (self.uuid, self.name)

    def translatable(self):
        """
        Wether we could extract any strings -> wether we can translate file
        """
        return (self.total_strings > 0)

    def update_props(self):
        """
        Try to parse the file and fill in information fields in current model
        """
        parser = None
        for p in PARSERS:
            if p.accept(self.name):
                parser = p
                break

        if not parser:
            return

        self.mime_type = parser.mime_type

        stringset = parser.parse_file(filename = self.get_storage_path()) 
        if not stringset:
            return

        if stringset.target_language:
            try:
                self.language = Language.objects.by_code_or_alias(stringset.target_language)
            except Language.DoesNotExist:
                pass

        self.total_strings = len(stringset.strings)
        return
