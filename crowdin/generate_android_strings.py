import os
import json
import xml.etree.ElementTree as ET
import sys
import argparse
import re
from pathlib import Path
from colorama import Fore, Style, init

# Variables that should be treated as numeric (using %d)
NUMERIC_VARIABLES = ['count', 'found_count', 'total_count']

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Convert a XLIFF translation files to Android XML.')
parser.add_argument('raw_translations_directory', help='Directory which contains the raw translation files')
parser.add_argument('translations_output_directory', help='Directory to save the converted translation files')
parser.add_argument('non_translatable_strings_output_path', help='Path to save the non-translatable strings to')
args = parser.parse_args()

INPUT_DIRECTORY = args.raw_translations_directory
TRANSLATIONS_OUTPUT_DIRECTORY = args.translations_output_directory
NON_TRANSLATABLE_STRINGS_OUTPUT_PATH = args.non_translatable_strings_output_path

def parse_xliff(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    namespace = {'ns': 'urn:oasis:names:tc:xliff:document:1.2'}
    translations = {}
    
    # Handle plural groups
    for group in root.findall('.//ns:group[@restype="x-gettext-plurals"]', namespaces=namespace):
        plural_forms = {}
        resname = None
        for trans_unit in group.findall('ns:trans-unit', namespaces=namespace):
            if resname is None:
                resname = trans_unit.get('resname')
            target = trans_unit.find('ns:target', namespaces=namespace)
            context_group = trans_unit.find('ns:context-group', namespaces=namespace)
            plural_form = context_group.find('ns:context[@context-type="x-plural-form"]', namespaces=namespace)
            if target is not None and target.text and plural_form is not None:
                form = plural_form.text.split(':')[-1].strip().lower()
                plural_forms[form] = target.text
        if resname and plural_forms:
            translations[resname] = plural_forms
    
    # Handle non-plural translations
    for trans_unit in root.findall('.//ns:trans-unit', namespaces=namespace):
        resname = trans_unit.get('resname')
        if resname not in translations:  # This is not part of a plural group
            target = trans_unit.find('ns:target', namespaces=namespace)
            if target is not None and target.text:
                translations[resname] = target.text
    
    return translations

def convert_placeholders(text):
    def repl(match):
        var_name = match.group(1)
        index = len(set(re.findall(r'\{([^}]+)\}', text[:match.start()]))) + 1
        
        if var_name in NUMERIC_VARIABLES:
            return f"%{index}$d"
        else:
            return f"%{index}$s"

    return re.sub(r'\{([^}]+)\}', repl, text)

def escape_android_string(text):
    # We can use standard XML escaped characters for most things (since XLIFF is an XML format) but
    # want the following cases escaped in a particulat way
    text = text.replace("'", r"\'")
    text = text.replace("&quot;", "\"")
    text = text.replace("\"", "\\\"")
    text = text.replace("&lt;b&gt;", "<b>")
    text = text.replace("&lt;/b&gt;", "</b>")
    text = text.replace("&lt;/br&gt;", "\\n")
    text = text.replace("<br/>", "\\n")
    return text

def generate_android_xml(translations, app_name):
    sorted_translations = sorted(translations.items())
    result = '<?xml version="1.0" encoding="utf-8"?>\n'
    result += '<resources>\n'

    if app_name is not None:
        result += f'    <string name="app_name" translatable="false">{app_name}</string>\n'

    for resname, target in sorted_translations:
        if isinstance(target, dict):  # It's a plural group
            result += f'    <plurals name="{resname}">\n'
            for form, value in target.items():
                escaped_value = escape_android_string(convert_placeholders(value))
                result += f'        <item quantity="{form}">{escaped_value}</item>\n'
            result += '    </plurals>\n'
        else:  # It's a regular string (for these we DON'T want to convert the placeholders)
            escaped_target = escape_android_string(target)
            result += f'    <string name="{resname}">{escaped_target}</string>\n'

    result += '</resources>'

    return result

def convert_xliff_to_android_xml(input_file, output_dir, source_locale, locale, app_name):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

    # Parse the XLIFF and convert to XML (only include the 'app_name' entry in the source language)
    is_source_language = (locale == source_locale)
    translations = parse_xliff(input_file)
    output_data = generate_android_xml(translations, app_name if is_source_language else None)

    # Generate output files
    language_code = locale.split('-')[0]
    region_code = locale.split('-')[1] if '-' in locale else None

    if is_source_language:
        language_output_dir = os.path.join(output_dir, 'values')
    else:
        language_output_dir = os.path.join(output_dir, f'values-{language_code}')

    os.makedirs(language_output_dir, exist_ok=True)
    language_output_file = os.path.join(language_output_dir, 'strings.xml')
    with open(language_output_file, 'w', encoding='utf-8') as file:
        file.write(output_data)

    if region_code:
        region_output_dir = os.path.join(output_dir, f'values-{language_code}-r{region_code}')
        os.makedirs(region_output_dir, exist_ok=True)
        region_output_file = os.path.join(region_output_dir, 'strings.xml')
        with open(region_output_file, 'w', encoding='utf-8') as file:
            file.write(output_data)

def convert_non_translatable_strings_to_kotlin(input_file, output_path):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

    # Process the non-translatable string input
    non_translatable_strings_data = {}
    with open(input_file, 'r') as file:
        non_translatable_strings_data = json.load(file)

    entries = non_translatable_strings_data['data']
    max_key_length = max(len(entry['data']['note'].upper()) for entry in entries)
    app_name = None

    # Output the file in the desired format
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as file:
        file.write('package org.session.libsession.utilities\n')
        file.write('\n')
        file.write('// Non-translatable strings for use with the UI\n')
        file.write("object NonTranslatableStringConstants {\n")
        for entry in entries:
            key = entry['data']['note'].upper()
            text = entry['data']['text']
            file.write(f'    const val {key:<{max_key_length}} = "{text}"\n')

            if key == 'APP_NAME':
                app_name = text

        file.write('}\n')
        file.write('\n')

    return app_name

def convert_all_files(input_directory):
    # Extract the project information
    print(f"\033[2K{Fore.WHITE}⏳ Processing project info...{Style.RESET_ALL}", end='\r')
    project_info_file = os.path.join(input_directory, "_project_info.json")
    if not os.path.exists(project_info_file):
        raise FileNotFoundError(f"Could not find '{project_info_file}' in raw translations directory")

    project_details = {}
    with open(project_info_file, 'r') as file:
        project_details = json.load(file)
    
    # Extract the language info and sort the target languages alphabetically by locale
    source_language = project_details['data']['sourceLanguage']
    target_languages = project_details['data']['targetLanguages']
    target_languages.sort(key=lambda x: x['locale'])
    num_languages = len(target_languages)
    print(f"\033[2K{Fore.GREEN}✅ Project info processed, {num_languages} languages will be converted{Style.RESET_ALL}")

    # Convert the non-translatable strings to the desired format
    print(f"\033[2K{Fore.WHITE}⏳ Generating static strings file...{Style.RESET_ALL}", end='\r')
    non_translatable_strings_file = os.path.join(input_directory, "_non_translatable_strings.json")
    app_name = convert_non_translatable_strings_to_kotlin(non_translatable_strings_file, NON_TRANSLATABLE_STRINGS_OUTPUT_PATH)
    print(f"\033[2K{Fore.GREEN}✅ Static string generation complete{Style.RESET_ALL}")

    # Convert the XLIFF data to the desired format
    print(f"\033[2K{Fore.WHITE}⏳ Converting translations to target format...{Style.RESET_ALL}", end='\r')
    source_locale = source_language['locale']
    for language in [source_language] + target_languages:
        lang_locale = language['locale']
        print(f"\033[2K{Fore.WHITE}⏳ Converting translations for {lang_locale} to target format...{Style.RESET_ALL}", end='\r')
        input_file = os.path.join(input_directory, f"{lang_locale}.xliff")
        convert_xliff_to_android_xml(input_file, TRANSLATIONS_OUTPUT_DIRECTORY, source_locale, lang_locale, app_name)
    print(f"\033[2K{Fore.GREEN}✅ All conversions complete{Style.RESET_ALL}")

if __name__ == "__main__":
    try:
        convert_all_files(INPUT_DIRECTORY)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\033[2K{Fore.RED}❌ An error occurred: {str(e)}")
        sys.exit(1)
