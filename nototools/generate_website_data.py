#!/usr/bin/python
# -*- coding: UTF-8 -*-
#
# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate data files for the Noto website."""

from __future__ import division

__author__ = 'roozbeh@google.com (Roozbeh Pournader)'

import codecs
import collections
import csv
import json
import locale
import os
from os import path
import re
import shutil
import subprocess
import xml.etree.cElementTree as ElementTree
import zipfile

from fontTools import ttLib

from nototools import coverage
from nototools import create_image
from nototools import font_data
from nototools import unicode_data

NOTO_DIR = path.abspath(path.join(path.dirname(__file__), os.pardir))

OUTPUT_DIR = path.join(NOTO_DIR, 'website_data')
CLDR_DIR = path.join(NOTO_DIR, 'third_party', 'cldr')
LAT_LONG_DIR = path.join(NOTO_DIR, 'third_party', 'dspl')
SAMPLE_TEXT_DIR = path.join(NOTO_DIR, 'sample_texts')
FONT_DIR = path.join(NOTO_DIR, 'fonts', 'individual')
CJK_DIR = path.join(NOTO_DIR, 'third_party', 'noto_cjk')


ODD_SCRIPTS = {
    'CJK': 'Qaak',  # private use code
    'JP': 'Jpan',
    'KR': 'Kore',
    'NKo': 'Nkoo',
    'Pahlavi': 'Phli',
    'Parthian': 'Prti',
    'SumeroAkkadianCuneiform': 'Xsux',
    'Symbols': 'Zsym',
}

def convert_to_four_letter(script):
    """"Converts a script name from a Noto font file name to ISO 15924 code."""
    if script in ODD_SCRIPTS:
        script = ODD_SCRIPTS[script]
    elif script in unicode_data._script_long_name_to_code:
        script = unicode_data._script_long_name_to_code[script]
    else:
        for lname in unicode_data._script_long_name_to_code:
            if lname.replace('_', '').lower() == script.lower():
                script = unicode_data._script_long_name_to_code[lname]
    assert len(script) in {0, 4}
    return script


Font = collections.namedtuple(
    'Font',
    'filepath, hint_status, key, '
    'family, script, variant, weight, style, platform,'
    'charset')


all_fonts = []
supported_scripts = set()

def find_fonts():
    font_name_regexp = re.compile(
        '(NotoSans|NotoSerif|NotoNaskh|NotoKufi|Arimo|Cousine|Tinos)'
        '(.*?)'
        '(UI|Eastern|Estrangela|Western)?'
        '-'
        '(|Black|Bold|DemiLight|Light|Medium|Regular|Thin)'
        '(Italic)?'
        '(-Windows)?'
        '.[ot]t[cf]')

    unicode_data.load_data()

    for directory in [path.join(FONT_DIR, 'hinted'),
                      path.join(FONT_DIR, 'unhinted'),
                      CJK_DIR]:
        for filename in os.listdir(directory):
            match = font_name_regexp.match(filename)
            if not match:
                assert (
                    filename.endswith('.ttx') or
                    filename.startswith('README.') or
                    filename in ['COPYING', 'LICENSE'])
                continue
            family, script, variant, weight, style, platform = match.groups()

            if family in {'Arimo', 'Cousine', 'Tinos'}:
                continue  # Skip these three for the website

            if family.startswith('Noto'):
                family = family.replace('Noto', 'Noto ')

            if weight == '':
                weight = 'Regular'

            if platform is not None:
                assert platform == '-Windows'
                platform = 'windows'

            if script == '':  # LGC
                supported_scripts.update({'Latn', 'Grek', 'Cyrl'})
            else:
                script = convert_to_four_letter(script)
                supported_scripts.add(script)

            if script == 'Qaak':
                continue  # Skip unified CJK fonts

            file_path = path.join(directory, filename)
            if filename.endswith('.ttf') or filename.endswith('.otf'):
                charset = coverage.character_set(file_path)
            else:
                charset = NotImplemented

            if directory == CJK_DIR:
                hint_status = 'hinted'
            else:
                hint_status = path.basename(directory)
            assert hint_status in ['hinted', 'unhinted']

            key = family.replace(' ', '-')
            if script:
                key += '-' + script
            if variant not in {None, 'UI'}:
                key += '-' + variant
            key = key.lower()

            font = Font(file_path, hint_status, key,
                        family, script, variant, weight, style, platform,
                        charset)
            all_fonts.append(font)


def read_character_at(source, pointer):
    assert source[pointer] not in ' -{}'
    if source[pointer] == '\\':
        if source[pointer+1] == 'u':
            end_of_hex = pointer+2
            while (end_of_hex < len(source)
                   and source[end_of_hex].upper() in '0123456789ABCDEF'):
                end_of_hex += 1
            assert end_of_hex-(pointer+2) in {4, 5, 6}
            hex_code = source[pointer+2:end_of_hex]
            return end_of_hex, unichr(int(hex_code, 16))
        else:
            return pointer+2, source[pointer+1]
    else:
        return pointer+1, source[pointer]


def exemplar_string_to_list(exstr):
    assert exstr[0] == '['
    exstr = exstr[1:]
    if exstr[-1] == ']':
        exstr = exstr[:-1]

    return_list = []
    pointer = 0
    while pointer < len(exstr):
        if exstr[pointer] in ' ':
            pointer += 1
        elif exstr[pointer] == '{':
            multi_char = ''
            mc_ptr = pointer+1
            while exstr[mc_ptr] != '}':
                mc_ptr, char = read_character_at(exstr, mc_ptr)
                multi_char += char
            return_list.append(multi_char)
            pointer = mc_ptr+1
        elif exstr[pointer] == '-':
            previous = return_list[-1]
            assert len(previous) == 1  # can't have ranges with strings
            previous = ord(previous)

            pointer, last = read_character_at(exstr, pointer+1)
            assert last not in [' ', '\\', '{', '}', '-']
            last = ord(last)
            return_list += [unichr(code) for code in range(previous+1, last+1)]
        else:
            pointer, char = read_character_at(exstr, pointer)
            return_list.append(char)

    return return_list


exemplar_from_file_cache = {}

def get_exemplar_from_file(cldr_file_path):
    try:
        return exemplar_from_file_cache[cldr_file_path]
    except KeyError:
        pass

    data_file = path.join(CLDR_DIR, cldr_file_path)
    try:
        root = ElementTree.parse(data_file).getroot()
    except IOError:
        exemplar_from_file_cache[cldr_file_path] = None
        return None
    for tag in root.iter('exemplarCharacters'):
        if 'type' in tag.attrib:
            continue
        exemplar_from_file_cache[cldr_file_path] = exemplar_string_to_list(
            tag.text)
        return exemplar_from_file_cache[cldr_file_path]
    return None


def find_parent_locale(locl):
    if locl in parent_locale:
        return parent_locale[locl]
    if '-' in locl:
        return locl[:locl.rindex('-')]
    if locale == 'root':
        return None
    return 'root'


def get_exemplar(language, script):
    locl = language + '-' + script
    while locl != 'root':
        for directory in ['common', 'seed', 'exemplars']:
            exemplar = get_exemplar_from_file(
                path.join(directory, 'main', locl.replace('-', '_')+'.xml'))
            if exemplar:
                return exemplar
        locl = find_parent_locale(locl)
    return None


def get_sample_from_sample_file(language, script):
    filepath = path.join(SAMPLE_TEXT_DIR, language+'-'+script+'.txt')
    if path.exists(filepath):
        return unicode(open(filepath).read().strip(), 'UTF-8')
    return None


language_name_from_file_cache = {}

def get_language_name_from_file(language, cldr_file_path):
    cache_key = (language, cldr_file_path)
    try:
        return language_name_from_file_cache[cache_key]
    except KeyError:
        pass

    data_file = path.join(CLDR_DIR, cldr_file_path)
    try:
        root = ElementTree.parse(data_file).getroot()
    except IOError:
        language_name_from_file_cache[cache_key] = None
        return None

    parent = root.find('.//languages')
    if parent is None:
        return None
    for tag in parent:
        assert tag.tag == 'language'
        if tag.get('type').replace('_', '-') == language:
            language_name_from_file_cache[cache_key] = tag.text
            return language_name_from_file_cache[cache_key]
    return None


_HARD_CODED_NATIVE_NAMES = {
    'mn-Mong': u'ᠮᠣᠨᠭᠭᠣᠯ ᠬᠡᠯᠡ',
}

def get_native_language_name(lang_scr):
    """Get the name of a language in its own locale."""
    try:
        return _HARD_CODED_NATIVE_NAMES[lang_scr]
    except KeyError:
        pass

    if '-' in lang_scr:
        language = lang_scr.split('-')[0]
    else:
        language = lang_scr

    locl = lang_scr
    while locl != 'root':
        for directory in ['common', 'seed']:
            file_path = path.join(
                directory, 'main', locl.replace('-', '_')+'.xml')
            for name_to_find in [lang_scr, language]:
                native_name = get_language_name_from_file(
                    name_to_find, file_path)
                if native_name:
                    return native_name
        locl = find_parent_locale(locl)
    return None


EXEMPLAR_CUTOFF_SIZE = 50

def sample_text_from_exemplar(exemplar):
    exemplar = [c for c in exemplar
                  if unicode_data.category(c[0])[0] in 'LNPS']
    exemplar = exemplar[:EXEMPLAR_CUTOFF_SIZE]
    return ' '.join(exemplar)


def get_sample_text(language, script):
    """Returns a sample text string for a given language and script."""

    sample_text = get_sample_from_sample_file(language, script)
    if sample_text is not None:
        return sample_text

    exemplar = get_exemplar(language, script)
    if exemplar is not None:
        return sample_text_from_exemplar(exemplar)

    sample_text = get_sample_from_sample_file('und', script)
    if sample_text is not None:
        return sample_text

    return ''


def xml_to_dict(element):
    return_dict = {}
    for child in list(element):
        if 'alt' in child.attrib:
            continue
        key = child.get('type')
        key = key.replace('_', '-')
        return_dict[key] = child.text
    return return_dict


english_language_name = {}
english_script_name = {}
english_territory_name = {}

def parse_english_labels():
    global english_language_name, english_script_name, english_territory_name

    data_file = path.join(
        CLDR_DIR, 'common', 'main', 'en.xml')
    root = ElementTree.parse(data_file).getroot()
    ldn = root.find('localeDisplayNames')

    english_language_name = xml_to_dict(ldn.find('languages'))
    english_script_name = xml_to_dict(ldn.find('scripts'))
    english_territory_name = xml_to_dict(ldn.find('territories'))

    # Add langauges used that miss names
    english_language_name.update({
        'abr': u'Abron',
        'abq': u'Abaza',
        'aii': u'Assyrian Neo-Aramaic',
        'akz': u'Alabama',
        'amo': u'Amo',
        'aoz': u'Uab Meto',
        'atj': u'Atikamekw',
        'bap': u'Bantawa',
        'bci': u'Baoulé',
        'bft': u'Balti',
        'bfy': u'Bagheli',
        'bgc': u'Haryanvi',
        'bgx': u'Balkan Gagauz Turkish',
        'bhb': u'Bhili',
        'bhi': u'Bhilali',
        'bhk': u'Albay Bikol',
        'bjj': u'Kanauji',
        'bku': u'Buhid',
        'blt': u'Tai Dam',
        'bmq': u'Bomu',
        'bqi': u'Bakhtiari',
        'bqv': u'Koro Wachi',
        'bsq': u'Bassa',
        'bto': u'Rinconada Bikol',
        'btv': u'Bateri',
        'buc': u'Bushi',
        'bvb': u'Bube',
        'bya': u'Batak',
        'bze': u'Jenaama Bozo',
        'bzx': u'Kelengaxo Bozo',
        'ccp': u'Chakma',
        'cja': u'Western Cham',
        'cjs': u'Shor',
        'cjm': u'Eastern Cham',
        'ckt': u'Chukchi',
        'crj': u'Southern East Cree',
        'crk': u'Plains Cree',
        'crl': u'Northern East Cree',
        'crm': u'Moose Cree',
        'crs': u'Seselwa Creole French',
        'csw': u'Swampy Cree',
        'ctd': u'Tedim Chin',
        'dcc': u'Deccan',
        'dng': u'Dungan',
        'dnj': u'Dan',
        'dtm': u'Tomo Kan Dogon',
        'eky': u'Eastern Kayah',
        'ett': u'Etruscan',
        'evn': u'Evenki',
        'ffm': u'Maasina Fulfulde',
        'fud': u'East Futuna',
        'fuq': u'Central-Eastern Niger Fulfulde',
        'fuv': u'Nigerian Fulfulde',
        'gbm': u'Garhwali',
        'gcr': u'Guianese Creole French',
        'ggn': u'Eastern Gurung',
        'gjk': u'Kachi Koli',
        'gju': u'Gujari',
        'gld': u'Nanai',
        'gos': u'Gronings',
        'grt': u'Garo',
        'gub': u'Guajajára',
        'gvr': u'Western Gurung',
        'haz': u'Hazaragi',
        'hmd': u'A-Hmao',
        'hnd': u'Southern Hindko',
        'hne': u'Chhattisgarhi',
        'hnj': u'Hmong Njua',
        'hnn': u'Hanunoo',
        'hno': u'Northern Hindko',
        'hoc': u'Ho',
        'hoj': u'Haroti',
        'hop': u'Hopi',
        'ikt': u'Inuinnaqtun',
        'jml': u'Jumli',
        'kao': u'Xaasongaxango',
        'kca': u'Khanty',
        'kck': u'Kalanga',
        'kdt': u'Kuy',
        'kfr': u'Kachchi',
        'kfy': u'Kumaoni',
        'kge': u'Komering',
        'khb': u'Lü',
        'khn': u'Khandesi',
        'kht': u'Khamti',
        'kjg': u'Khmu',
        'kjh': u'Khakas',
        'kpy': u'Koryak',
        'kvr': u'Kerinci',
        'kvx': u'Parkari Koli',
        'kxm': u'Northern Khmer',
        'kxp': u'Wadiyara Koli',
        'laj': u'Lango',
        'lbe': u'Lak',
        'lbw': u'Tolaki',
        'lcp': u'Western Lawa',
        'lep': u'Lepcha',
        'lif': u'Limbu',
        'lis': u'Lisu',
        'ljp': u'Lampung Api',
        'lki': u'Laki',
        'lmn': u'Lambadi',
        'lrc': u'Northern Luri',
        'luz': u'Southern Luri',
        'lwl': u'Eastern Lawa',
        'maz': u'Central Mazahua',
        'mdh': u'Maguindanaon',
        'mfa': u'Pattani Malay',
        'mgp': u'Eastern Magar',
        'mgy': u'Mbunga',
        'mnw': u'Mon',
        'moe': u'Montagnais',
        'mrd': u'Western Magar',
        'mtr': u'Mewari',
        'mvy': u'Indus Kohistani',
        'mwk': u'Kita Maninkakan',
        'mxc': u'Manyika',
        'myx': u'Masaaba',
        'nch': u'Central Huasteca Nahuatl',
        'ndc': u'Ndau',
        'ngl': u'Lomwe',
        'nhe': u'Eastern Huasteca Nahuatl',
        'nhw': u'Western Huasteca Nahuatl',
        'nij': u'Ngaju',
        'nod': u'Northern Thai',
        'noe': u'Nimadi',
        'nsk': u'Naskapi',
        'nxq': u'Naxi',
        'pcm': u'Nigerian Pidgin',
        'pko': u'Pökoot',
        'prd': u'Parsi-Dari',
        'puu': u'Punu',
        'rcf': u'Réunion Creole French',
        'rej': u'Rejang',
        'ria': u'Riang',  # (India)
        'rjs': u'Rajbanshi',
        'rkt': u'Rangpuri',
        'rmf': u'Kalo Finnish Romani',
        'rmo': u'Sinte Romani',
        'rmt': u'Domari',
        'rmu': u'Tavringer Romani',
        'rng': u'Ronga',
        'rob': u'Tae’',
        'ryu': u'Central Okinawan',
        'saf': u'Safaliba',
        'sck': u'Sadri',
        'scs': u'North Slavey',
        'sdh': u'Southern Kurdish',
        'sef': u'Cebaara Senoufo',
        'skr': u'Seraiki',
        'sou': u'Southern Thai',
        'srx': u'Sirmauri',
        'swv': u'Shekhawati',
        'sxn': u'Sangir',
        'syl': u'Sylheti',
        'taj': u'Eastern Tamang',
        'tbw': u'Tagbanwa',
        'tdd': u'Tai Nüa',
        'tdg': u'Western Tamang',
        'tdh': u'Thulung',
        'thl': u'Dangaura Tharu',
        'thq': u'Kochila Tharu',
        'thr': u'Rana Tharu',
        'tkt': u'Kathoriya Tharu',
        'tsf': u'Southwestern Tamang',
        'tsg': u'Tausug',
        'tsj': u'Tshangla',
        'ttj': u'Tooro',
        'tts': u'Northeastern Thai',
        'uli': u'Ulithian',
        'unr': u'Mundari',
        'unx': u'Munda',
        'vic': u'Virgin Islands Creole English',
        'vmw': u'Makhu',
        'wbr': u'Wagdi',
        'wbq': u'Waddar',
        'wls': u'Wallisian',
        'wtm': u'Mewati',
        'xav': u'Xavánte',
        'xnr': u'Kangri',
        'xsr': u'Sherpa',
        'yua': u'Yucatec Maya',
        'zdj': u'Ngazidja Comorian',
        'zmi': u'Negeri Sembilan Malay',
    })

def get_english_language_name(lang_scr):
    try:
        return english_language_name[lang_scr]
    except KeyError:
        print 'Constructing a name for %s.' % lang_scr
        lang, script = lang_scr.split('-')
        return '%s (%s script)' % (
            english_language_name[lang],
            english_script_name[script])


used_in_regions = collections.defaultdict(set)
written_in_scripts = collections.defaultdict(set)
territory_info = collections.defaultdict(set)
parent_locale = {}

def parse_supplemental_data():
    data_file = path.join(
        CLDR_DIR, 'common', 'supplemental', 'supplementalData.xml')
    root = ElementTree.parse(data_file).getroot()

    for language_tag in root.iter('language'):
        attribs = language_tag.attrib

        if 'alt' in attribs:
            assert attribs['alt'] == 'secondary'

        lang = attribs['type']

        if 'territories' in attribs:
            territories = set(attribs['territories'].split(' '))
            used_in_regions[lang].update(territories)

        if 'scripts' in attribs:
            scripts = set(attribs['scripts'].split(' '))
            written_in_scripts[lang].update(scripts)

    for tag in root.iter('territory'):
        territory = tag.get('type')
        for child in tag:
            assert child.tag == 'languagePopulation'
#            if 'officialStatus' not in child.attrib:
#                continue  # Skip non-official languages
            lang = child.get('type').replace('_', '-')
            territory_info[territory].add(lang)

    for tag in root.iter('parentLocale'):
        parent = tag.get('parent')
        parent = parent.replace('_', '-')
        for locl in tag.get('locales').split(' '):
            locl = locl.replace('_', '-')
            parent_locale[locl] = parent

    parent_locale.update({
        'ky-Latn': 'root',
        'sd-Deva': 'root',
        'tg-Arab': 'root',
        'ug-Cyrl': 'root',
    })


likely_subtag_data = {}

def parse_likely_subtags():
    data_file = path.join(
        CLDR_DIR, 'common', 'supplemental', 'likelySubtags.xml')
    tree = ElementTree.parse(data_file)

    for tag in tree.findall('likelySubtags/likelySubtag'):
        from_tag = tag.get('from').replace('_', '-')
        to_tag = tag.get('to').split('_')
        likely_subtag_data[from_tag] = to_tag

    likely_subtag_data.update({
        'abr': ('abr', 'Latn', 'GH'),  # Abron
        'abq': ('abq', 'Cyrl', 'RU'),  # Abaza
        'ada': ('ada', 'Latn', 'GH'),  # Adangme
        'ae':  ('ae',  'Avst', 'ZZ'),  # Avestan
        'aeb': ('aeb', 'Arab', 'TN'),  # Tunisian Arabic
        'aii': ('aii', 'Syrc', 'IQ'),  # Assyrian Neo-Aramaic
        'ain': ('ain', 'Kana', 'JP'),  # Ainu
        'akk': ('akk', 'Xsux', 'ZZ'),  # Akkadian
        'akz': ('akz', 'Latn', 'US'),  # Alabama
        'ale': ('ale', 'Latn', 'US'),  # Aleut
        'aln': ('aln', 'Latn', 'XK'),  # Gheg Albanian
        'an':  ('an',  'Latn', 'ES'),  # Aragonese
        'anp': ('anp', 'Deva', 'IN'),  # Angika
        'arc': ('arc', 'Armi', 'ZZ'),  # Imperial Aramaic
        'aro': ('aro', 'Latn', 'BO'),  # Araona
        'arp': ('arp', 'Latn', 'US'),  # Arapaho
        'arq': ('arq', 'Arab', 'DZ'),  # Algerian Arabic
        'arw': ('arw', 'Latn', 'GY'),  # Arawak
        'ary': ('ary', 'Arab', 'MA'),  # Moroccan Arabic
        'arz': ('arz', 'Arab', 'EG'),  # Egyptian Arabic
        'avk': ('avk', 'Latn', '001'),  # Kotava
        'azb': ('azb', 'Arab', 'IR'),  # Southern Azerbaijani
        'bar': ('bar', 'Latn', 'AT'),  # Bavarian
        'bej': ('bej', 'Arab', 'SD'),  # Beja
        'bci': ('bci', 'Latn', 'CI'),  # Baoulé
        'bgc': ('bgc', 'Deva', 'IN'),  # Haryanvi
        'bhi': ('bhi', 'Deva', 'IN'),  # Bhilali
        'bhk': ('bhk', 'Latn', 'PH'),  # Albay Bikol
        'bla': ('bla', 'Latn', 'CA'),  # Blackfoot
        'blt': ('blt', 'Tavt', 'VN'),  # Tai Dam
        'bpy': ('bpy', 'Beng', 'IN'),  # Bishnupriya
        'bqi': ('bqi', 'Arab', 'IR'),  # Bakhtiari
        'bsq': ('bsq', 'Bass', 'LR'),  # Bassa
        'bzx': ('bzx', 'Latn', 'ML'),  # Kelengaxo Bozo
        'cad': ('cad', 'Latn', 'US'),  # Caddo
        'car': ('car', 'Latn', 'VE'),  # Galibi Carib
        'cay': ('cay', 'Latn', 'CA'),  # Cayuga
        'chn': ('chn', 'Latn', 'US'),  # Chinook Jargon
        'cho': ('cho', 'Latn', 'US'),  # Choctaw
        'chy': ('chy', 'Latn', 'US'),  # Cheyenne
        'cjs': ('cjs', 'Cyrl', 'RU'),  # Shor
        'ckt': ('ckt', 'Cyrl', 'RU'),  # Chukchi
        'cop': ('cop', 'Copt', 'EG'),  # Coptic
        'cps': ('cps', 'Latn', 'PH'),  # Capiznon
        'crh': ('crh', 'Latn', 'UA'),  # Crimean Tatar
        'crs': ('crs', 'Latn', 'SC'),  # Seselwa Creole French
        'ctd': ('ctd', 'Latn', 'MM'),  # Tedim Chin
        'dak': ('dak', 'Latn', 'US'),  # Dakota
        'dcc': ('dcc', 'Arab', 'IN'),  # Deccan
        'del': ('del', 'Latn', 'US'),  # Delaware
        'din': ('din', 'Latn', 'SS'),  # Dinka
        'dng': ('dng', 'Cyrl', 'KG'),  # Dungan
        'dtp': ('dtp', 'Latn', 'MY'),  # Central Dusun
        'egl': ('egl', 'Latn', 'IT'),  # Emilian
        'egy': ('egy', 'Egyp', 'ZZ'),  # Ancient Egyptian
        'eka': ('eka', 'Egyp', 'NG'),  # Ekajuk
        'eky': ('eky', 'Kali', 'TH'),  # Eastern Kayah
        'esu': ('esu', 'Latn', 'US'),  # Central Yupik
        'ett': ('ett', 'Ital', 'IT'),  # Etruscan
        'evn': ('evn', 'Latn', 'CN'),  # Evenki
        'ext': ('ext', 'Latn', 'ES'),  # Extremaduran
        'ffm': ('ffm', 'Latn', 'ML'),  # Maasina Fulfulde
        'frc': ('frc', 'Latn', 'US'),  # Cajun French
        'frr': ('frr', 'Latn', 'DE'),  # Northern Frisian
        'frs': ('frs', 'Latn', 'DE'),  # Eastern Frisian
        'fud': ('fud', 'Latn', 'WF'),  # East Futuna
        'fuq': ('fuq', 'Latn', 'NE'),  # Central-Eastern Niger Fulfulde
        'fuv': ('fuv', 'Latn', 'NG'),  # Nigerian Fulfulde
        'gan': ('gan', 'Hans', 'CN'),  # Gan Chinese
        'gay': ('gay', 'Latn', 'ID'),  # Gayo
        'gba': ('gba', 'Latn', 'CF'),  # Gbaya
        'gbz': ('gbz', 'Arab', 'IR'),  # Zoroastrian Dari
        'gld': ('gld', 'Cyrl', 'RU'),  # Nanai
        'gom': ('gom', 'Deva', 'IN'),  # Goan Konkani
        'got': ('got', 'Goth', 'ZZ'),  # Gothic
        'grb': ('grb', 'Latn', 'LR'),  # Grebo
        'grc': ('grc', 'Grek', 'ZZ'),  # Ancient Greek
        'guc': ('guc', 'Latn', 'CO'),  # Wayuu
        'gur': ('gur', 'Latn', 'GH'),  # Frafra
        'hai': ('hai', 'Latn', 'CA'),  # Haida
        'hak': ('hak', 'Hant', 'CN'),  # Hakka Chinese
        'haz': ('haz', 'Arab', 'AF'),  # Hazaragi
        'hif': ('hif', 'Deva', 'FJ'),  # Fiji Hindi
        'hit': ('hit', 'Xsux', 'ZZ'),  # Hittite
        'hmd': ('hmd', 'Plrd', 'CN'),  # A-Hmao
        'hmn': ('hmn', 'Latn', 'CN'),  # Hmong
        'hnj': ('hnj', 'Latn', 'LA'),  # Hmong Njua
        'hno': ('hno', 'Arab', 'PK'),  # Northern Hindko
        'hop': ('hop', 'Latn', 'US'),  # Hopi
        'hsn': ('hsn', 'Hans', 'CN'),  # Xiang Chinese
        'hup': ('hup', 'Latn', 'US'),  # Hupa
        'hz':  ('hz',  'Latn', 'NA'),  # Herero
        'iba': ('iba', 'Latn', 'MY'),  # Iban
        'ikt': ('ikt', 'Latn', 'CA'),  # Inuinnaqtun
        'izh': ('izh', 'Latn', 'RU'),  # Ingrian
        'jam': ('jam', 'Latn', 'JM'),  # Jamaican Creole English
        'jpr': ('jpr', 'Hebr', 'IL'),  # Judeo-Persian
        'jrb': ('jrb', 'Hebr', 'IL'),  # Jedeo-Arabic
        'jut': ('jut', 'Latn', 'DK'),  # Jutish
        'kac': ('kac', 'Latn', 'MM'),  # Kachin
        'kca': ('kca', 'Cyrl', 'RU'),  # Khanty
        'kfy': ('kfy', 'Deva', 'IN'),  # Kumaoni
        'kjh': ('kjh', 'Cyrl', 'RU'),  # Khakas
        'khn': ('khn', 'Deva', 'IN'),  # Khandesi
        'kiu': ('kiu', 'Latn', 'TR'),  # Kirmanjki
        'kpy': ('kpy', 'Cyrl', 'RU'),  # Koryak
        'kxm': ('kxm', 'Thai', 'TH'),  # Northern Khmer
        'laj': ('laj', 'Latn', 'UG'),  # Lango
        'ljp': ('ljp', 'Latn', 'ID'),  # Lampung Api
        'lrc': ('lrc', 'Arab', 'IR'),  # Northern Luri
        'mfa': ('mfa', 'Arab', 'TH'),  # Pattani Malay
        'mtr': ('mtr', 'Deva', 'IN'),  # Mewari
        'mwl': ('mwl', 'Latn', 'PT'),  # Mirandese
        'mwv': ('mwv', 'Latn', 'ID'),  # Mentawai
        'myx': ('myx', 'Latn', 'UG'),  # Masaaba
        'ndc': ('ndc', 'Latn', 'MZ'),  # Ndau
        'ngl': ('ngl', 'Latn', 'MZ'),  # Lomwe
        'noe': ('noe', 'Deva', 'IN'),  # Nimadi
        'osa': ('osa', 'Latn', 'US'),  # Osage
        'rom': ('rom', 'Latn', 'RO'),  # Romany
        'sck': ('sck', 'Deva', 'IN'),  # Sadri
        'skr': ('skr', 'Arab', 'PK'),  # Seraiki
        'sou': ('sou', 'Thai', 'TH'),  # Southern Thai
        'swv': ('swv', 'Deva', 'IN'),  # Shekhawati
        'uga': ('uga', 'Ugar', 'ZZ'),  # Ugaritic
        'vep': ('vep', 'Latn', 'RU'),  # Veps
        'vmw': ('vmw', 'Latn', 'MZ'),  # Makhuwa
        'wbr': ('wbr', 'Deva', 'IN'),  # Wagdi
        'wbq': ('wbq', 'Telu', 'IN'),  # Waddar
        'wls': ('wls', 'Latn', 'WF'),  # Wallisian
        'wtm': ('wtm', 'Deva', 'IN'),  # Mewati
        'xnr': ('xnr', 'Deva', 'IN'),  # Kangri
        'zdj': ('zdj', 'Arab', 'KM'),  # Ngazidja Comorian
    })


def find_likely_script(language):
    if not likely_subtag_data:
        parse_likely_subtags()
    return likely_subtag_data[language][1]


script_metadata = {}

def parse_script_metadata():
    global script_metadata
    data = open(path.join(
        CLDR_DIR, 'common', 'properties', 'scriptMetadata.txt')).read()
    parsed_data = unicode_data._parse_semicolon_separated_data(data)
    script_metadata = {line[0]:tuple(line[1:]) for line in parsed_data}


def is_script_rtl(script):
    if not script_metadata:
        parse_script_metadata()
    return script_metadata[script][5] == 'YES'


lat_long_data = {}

def read_lat_long_data():
    with open(path.join(LAT_LONG_DIR, 'countries.csv')) as lat_long_file:
        for row in csv.reader(lat_long_file):
            region, latitude, longitude, _ = row
            if region == 'country':
                continue  # Skip the header
            if not latitude:
                continue  # Empty latitude
            latitude = float(latitude)
            longitude = float(longitude)
            lat_long_data[region] = (latitude, longitude)

    # From the English Wikipedia and The World Factbook at
    # https://www.cia.gov/library/publications/the-world-factbook/fields/2011.html
    lat_long_data.update({
        'AC': (-7-56/60, -14-22/60),  # Ascension Island
        'AX': (60+7/60, 19+54/60),  # Åland Islands
        'BL': (17+54/60, -62-50/60),  # Saint Barthélemy
        'BQ': (12+11/60, -68-14/60),  # Caribbean Netherlands
        'CP': (10+18/60, -109-13/60),  # Clipperton Island
        'CW': (12+11/60, -69),  # Curaçao
        'DG': (7+18/60+48/3600, 72+24/60+40/3600),  # Diego Garcia
         # Ceuta and Melilla, using Ceuta
        'EA': (35+53/60+18/3600, -5-18/60-56/3600),
        'IC': (28.1, -15.4),  # Canary Islands
        'MF': (18+4/60+31/3600, -63-3/60-36/3600),  # Saint Martin
        'SS': (8, 30),  # South Sudan
        'SX': (18+3/60, -63-3/60),  # Sint Maarten
        'TA': (-37-7/60, -12-17/60),  # Tristan da Cunha
         # U.S. Outlying Islands, using Johnston Atoll
        'UM': (16+45/60, -169-31/60),
    })


def sorted_langs(langs):
    return sorted(
        set(langs),
        key=lambda code: locale.strxfrm(
            get_english_language_name(code).encode('UTF-8')))


all_used_lang_scrs = set()

def create_regions_object():
    if not lat_long_data:
        read_lat_long_data()
    regions = {}
    for territory in territory_info:
        region_obj = {}
        region_obj['name'] = english_territory_name[territory]
        region_obj['lat'], region_obj['lng'] = lat_long_data[territory]
        region_obj['langs'] = sorted_langs(territory_info[territory])
        all_used_lang_scrs.update(territory_info[territory])
        regions[territory] = region_obj

    return regions


def charset_supports_text(charset, text):
    if charset is NotImplemented:
        return False
    needed_codepoints = {ord(char) for char in set(text)}
    return needed_codepoints <= charset


family_to_langs = collections.defaultdict(set)

def create_langs_object():
    langs = {}
#    # Try all languages of which we know the script
#    for lang_scr in sorted(written_in_scripts):
    for lang_scr in all_used_lang_scrs:
        lang_object = {}
        if '-' in lang_scr:
            language, script = lang_scr.split('-')
        else:
            language = lang_scr
            script = find_likely_script(language)

        lang_object['name'] = get_english_language_name(lang_scr)
        native_name = get_native_language_name(lang_scr)
        if native_name is not None:
            lang_object['nameNative'] = native_name

        lang_object['rtl'] = is_script_rtl(script)

        if script == 'Kana':
            script = 'Jpan'

        if script not in supported_scripts:
            # Scripts we don't have fonts for yet
            print('No font supports the %s script needed for '
                  'the %s language.' % (script, lang_object['name']))
            assert script in {'Bass', 'Orya', 'Plrd', 'Thaa', 'Tibt'}

            lang_object['families'] = []
        else:
            sample_text = get_sample_text(language, script)
            lang_object['sample'] = sample_text

            if script in {'Latn', 'Grek', 'Cyrl'}:
                query_script = ''
            else:
                query_script = script

            # FIXME(roozbeh): Figure out if the language is actually supported
            # by the font + Noto LGC. If it's not, don't claim support.
            fonts = [font for font in all_fonts if font.script == query_script]
            family_keys = set([font.key for font in fonts])

            lang_object['families'] = sorted(family_keys)
            for family in family_keys:
                family_to_langs[family].add(lang_scr)

        langs[lang_scr] = lang_object
    return langs


def get_font_family_name(font_file):
    font = ttLib.TTFont(font_file)
    name_record = font_data.get_name_records(font)
    return name_record[1]


def charset_to_ranges(font_charset):
    # Ignore basic common characters
    charset = font_charset - {0x00, 0x0D, 0x20, 0xA0, 0xFEFF}
    ranges = coverage.convert_set_to_ranges(charset)

    output_list = []
    for start, end in ranges:
        output_list.append(('%04X' % start, '%04X' % end))
    return output_list


def get_css_generic_family(family):
    if family in {'Noto Naskh', 'Noto Serif', 'Tinos'}:
        return 'serif'
    if family in {'Arimo', 'Noto Kufi', 'Noto Sans'}:
        return 'sans-serif'
    if family == 'Cousine':
        return 'monospace'
    return None


CSS_WEIGHT_MAPPING = {
    'Thin': 100,
    'Light': 300,
    'DemiLight': 350,
    'Regular': 400,
    'Medium': 500,
    'Bold': 700,
    'Black': 900,
}

def css_weight(weight_string):
    return CSS_WEIGHT_MAPPING[weight_string]


CSS_WEIGHT_TO_STRING = {s:w for w, s in CSS_WEIGHT_MAPPING.items()}

def css_weight_to_string(weight):
    return CSS_WEIGHT_TO_STRING[weight]


def css_style(style_value):
    if style_value is None:
        return 'normal'
    else:
        assert style_value == 'Italic'
        return 'italic'


def fonts_are_basically_the_same(font1, font2):
    """Returns true if the fonts are the same, except perhaps hint or platform.
    """
    return (font1.family == font2.family and
            font1.script == font2.script and
            font1.variant == font2.variant and
            font1.weight == font2.weight and
            font1.style == font2.style)


def compress_png(pngpath):
    subprocess.call(['optipng', '-o7', '-quiet', pngpath])


def recompress_zip(zippath):
    dev_null = open(os.devnull, 'w')
    subprocess.call(['advzip', '-z', '-4', zippath], stdout=dev_null)


def compress(filepath, compress_function):
    print 'Compressing %s.' % filepath
    oldsize = os.stat(filepath).st_size
    compress_function(filepath)
    newsize = os.stat(filepath).st_size
    print 'Compressed from {0:,}B to {1:,}B.'.format(oldsize, newsize)


zip_contents_cache = {}

def create_zip(major_name, target_platform, fonts):
    # Make sure no file name repeats
    assert len({path.basename(font.filepath) for font in fonts}) == len(fonts)

    all_hint_statuses = {font.hint_status for font in fonts}
    if len(all_hint_statuses) == 1:
        hint_status = list(all_hint_statuses)[0]
    else:
        hint_status = 'various'

    if target_platform == 'other':
        if hint_status == 'various':
            # This may only be the comprehensive package
            assert len(fonts) > 50
            suffix = ''
        elif hint_status == 'unhinted':
            suffix = '-unhinted'
        else:  # hint_status == 'hinted'
            suffix = '-hinted'
    elif target_platform == 'windows':
        if hint_status in ['various', 'hinted']:
            if 'windows' in {font.platform for font in fonts}:
                suffix = '-windows'
            else:
                suffix = '-hinted'
        else:  # hint_status == 'unhinted':
            suffix = '-unhinted'
    else:  # target_platform == 'linux'
        if len(fonts) > 50 or hint_status in ['various', 'hinted']:
            suffix = '-hinted'
        else:
            suffix = '-unhinted'

    zip_basename = '%s%s.zip' % (major_name, suffix)

    zippath = path.join(OUTPUT_DIR, 'pkgs', zip_basename)
    frozen_fonts = frozenset(fonts)
    if path.isfile(zippath):  # Skip if the file already exists
        assert zip_contents_cache[zip_basename] == frozen_fonts
    else:
        assert frozen_fonts not in zip_contents_cache.values()
        zip_contents_cache[zip_basename] = frozen_fonts
        with zipfile.ZipFile(zippath, 'w', zipfile.ZIP_DEFLATED) as output_zip:
            for font in fonts:
                output_zip.write(font.filepath, path.basename(font.filepath))
    compress(zippath, recompress_zip)
    return zip_basename


def copy_font(source_file):
    source_dir, source_basename = path.split(source_file)
    target_dir = path.join(OUTPUT_DIR, 'fonts')
    if source_dir.endswith('/hinted'):
        target_dir = path.join(target_dir, 'hinted')
    shutil.copy(source_file, path.join(OUTPUT_DIR, target_dir))
    return '../fonts/' + source_basename


def create_css(key, family_name, fonts):
    csspath = path.join(OUTPUT_DIR, 'css', 'fonts', key + '.css')
    with open(csspath, 'w') as css_file:
        for font in fonts:
            font_url = copy_font(font.filepath)
            css_file.write(
                '@font-face {\n'
                '  font-family: "%s";\n'
                '  font-weight: %d;\n'
                '  font-style: %s;\n'
                '  src: url(%s) format("truetype");\n'
                '}\n' % (
                    family_name,
                    css_weight(font.weight),
                    css_style(font.style),
                    font_url)
            )
    return '%s.css' % key


def create_families_object(target_platform):
    all_keys = set([font.key for font in all_fonts])
    families = {}
    all_font_files = set()
    for key in all_keys:
        family_object = {}
        members = {font for font in all_fonts
                   if font.key == key and font.variant != 'UI'
                                      and font.filepath.endswith('tf')}

        members_to_drop = set()
        for font in members:
            if font.platform == target_platform:
                # If there are any members matching the target platform, they
                # take priority: drop alternatives
                members_to_drop.update(
                    {alt for alt in members
                     if fonts_are_basically_the_same(font, alt) and
                        font.platform != alt.platform})
            elif font.platform is not None:
                # This is a font for another platform
                members_to_drop.add(font)
        members -= members_to_drop

        if target_platform in ['windows', 'linux']:
            desired_hint_status = 'hinted'
        else:
            desired_hint_status = 'unhinted'

        # If there are any members matching the desired hint status, they take
        # priority: drop alternatives
        members_to_drop = set()
        for font in members:
            if font.hint_status == desired_hint_status:
                members_to_drop.update(
                    {alt for alt in members
                     if fonts_are_basically_the_same(font, alt) and
                        font.hint_status != alt.hint_status})
        members -= members_to_drop

        all_font_files |= members

        repr_members = {font for font in members
                        if font.weight == 'Regular' and font.style is None}
        assert len(repr_members) == 1
        repr_member = repr_members.pop()

        font_family_name = get_font_family_name(repr_member.filepath)
        if font_family_name.endswith('Regular'):
            font_family_name = font_family_name.rsplit(' ', 1)[0]
        family_object['name'] = font_family_name

        family_object['pkg'] = create_zip(
            font_family_name.replace(' ', ''), target_platform, members)

        family_object['langs'] = sorted_langs(family_to_langs[repr_member.key])

        family_object['category'] = get_css_generic_family(repr_member.family)
        family_object['css'] = create_css(key, font_family_name, members)
        family_object['ranges'] = charset_to_ranges(repr_member.charset)

        font_list = []
        for font in members:
            font_list.append({
                'style': css_style(font.style),
                'weight': css_weight(font.weight),
            })
        if len(font_list) not in [1, 2, 4, 7]:
            print key, font_list
        assert len(font_list) in [1, 2, 4, 7]
        family_object['fonts'] = font_list

        families[key] = family_object
    return families, all_font_files


def generate_sample_images(data_object):
    image_dir = path.join(OUTPUT_DIR, 'images', 'samples')
    for family_key in data_object['family']:
        family_obj = data_object['family'][family_key]
        font_family_name = family_obj['name']
        print 'Generating images for %s...' % font_family_name
        is_cjk_family = (
            family_key.endswith('-hans') or
            family_key.endswith('-hant') or
            family_key.endswith('-jpan') or
            family_key.endswith('-kore'))
        for lang_scr in family_obj['langs']:
            lang_obj = data_object['lang'][lang_scr]
            sample_text = lang_obj['sample']
            is_rtl = lang_obj['rtl']
            for instance in family_obj['fonts']:
                weight, style = instance['weight'], instance['style']
                image_file_name = path.join(
                    image_dir,
                    '%s_%s_%d_%s.png' % (family_key, lang_scr, weight, style))
                if is_cjk_family:
                    family_suffix = ' ' + css_weight_to_string(weight)
                else:
                    family_suffix = ''
                image_location = path.join(image_dir, image_file_name)
                create_image.create_png(
                    sample_text,
                    image_location,
                    family=font_family_name+family_suffix,
                    language=lang_scr,
                    rtl=is_rtl,
                    weight=weight, style=style)
                compress(image_location, compress_png)


def create_package_object(fonts, target_platform):
    comp_zip_file = create_zip('Noto', target_platform, fonts)

    package = {}
    package['url'] = comp_zip_file
    package['size'] = os.stat(
        path.join(OUTPUT_DIR, 'pkgs', comp_zip_file)).st_size
    return package


def main():
    """Outputs data files for the noto website."""

    if path.exists(OUTPUT_DIR):
        assert path.isdir(OUTPUT_DIR)
        print 'Removing the old website directory...'
        shutil.rmtree(OUTPUT_DIR)
    os.mkdir(OUTPUT_DIR)
    os.mkdir(path.join(OUTPUT_DIR, 'pkgs'))
    os.mkdir(path.join(OUTPUT_DIR, 'fonts'))
    os.mkdir(path.join(OUTPUT_DIR, 'fonts', 'hinted'))
    os.mkdir(path.join(OUTPUT_DIR, 'css'))
    os.mkdir(path.join(OUTPUT_DIR, 'css', 'fonts'))
    os.mkdir(path.join(OUTPUT_DIR, 'images'))
    os.mkdir(path.join(OUTPUT_DIR, 'images', 'samples'))
    os.mkdir(path.join(OUTPUT_DIR, 'js'))

    print 'Finding all fonts...'
    find_fonts()

    print 'Parsing CLDR data...'
    parse_english_labels()
    parse_supplemental_data()

    for target_platform in ['windows', 'linux', 'other']:
        print 'Target platform %s:' % target_platform

        output_object = {}
        print 'Generating data objects and CSS...'
        output_object['region'] = create_regions_object()
        output_object['lang'] = create_langs_object()
        output_object['family'], all_font_files = create_families_object(
            target_platform)

        print 'Creating comprehensive zip file...'
        output_object['pkg'] = create_package_object(
            all_font_files, target_platform)

        ############### Hot patches ###############
        # Kufi is broken for Urdu Heh goal
        output_object['lang']['ur']['families'].remove('noto-kufi-arab')
        output_object['family']['noto-kufi-arab']['langs'].remove('ur')

        # Kufi doesn't support all characters needed for Khowar
        output_object['lang']['khw']['families'].remove('noto-kufi-arab')
        output_object['family']['noto-kufi-arab']['langs'].remove('khw')

        # Kufi doesn't support all characters needed for Kashmiri
        output_object['lang']['ks-Arab']['families'].remove('noto-kufi-arab')
        output_object['family']['noto-kufi-arab']['langs'].remove('ks-Arab')
        ############### End of hot patches ########

        if target_platform == 'linux':
            generate_sample_images(output_object)

        # Drop presently unused features
        for family in output_object['family'].itervalues():
            del family['category']
            del family['css']
            del family['ranges']
        for language in output_object['lang'].itervalues():
            del language['rtl']
            if 'sample' in language:
                del language['sample']

        json_path = path.join(OUTPUT_DIR, 'js', 'data-%s.json'%target_platform)
        with codecs.open(json_path, 'w', encoding='UTF-8') as json_file:
            json.dump(output_object, json_file,
                      ensure_ascii=False, separators=(',', ':'))

    # Drop presently unused directories
    shutil.rmtree(path.join(OUTPUT_DIR, 'fonts'))
    shutil.rmtree(path.join(OUTPUT_DIR, 'css'))


if __name__ == '__main__':
    locale.setlocale(locale.LC_COLLATE, 'en_US.UTF-8')
    main()
