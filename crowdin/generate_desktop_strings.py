import os
import json
import xml.etree.ElementTree as ET
import sys
import argparse
import html
from pathlib import Path
from colorama import Fore, Style, init

# Customizable mapping for output folder hierarchy
# Add entries here to customize the output path for specific locales
# Format: 'input_locale': 'output_path'
LOCALE_PATH_MAPPING = {
    'en-US': 'en',
    'kmr-TR': 'kmr',
    # Note: we don't want to replace - with _ anymore.
    # We still need those mappings, otherwise they fallback to their 2 letter codes
    'es-419': 'es-419',
    'hy-AM': 'hy-AM',
    'pt-BR': 'pt-BR',
    'pt-PT': 'pt-PT',
    'zh-CN': 'zh-CN',
    'zh-TW': 'zh-TW'
    # Add more mappings as needed
}

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Convert a XLIFF translation files to JSON.')
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

def generate_icu_pattern(target):
    if isinstance(target, dict):  # It's a plural group
        pattern_parts = []
        for form, value in target.items():
            if form in ['zero', 'one', 'two', 'few', 'many', 'other', 'exact', 'fractional']:
                # Replace {count} with #
                value = html.unescape(value.replace('{count}', '#'))
                pattern_parts.append(f"{form} [{value}]")
        
        return "{{count, plural, {0}}}".format(" ".join(pattern_parts))
    else:  # It's a regular string
        return html.unescape(target)

def convert_xliff_to_json(input_file, output_dir, locale, locale_two_letter_code):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

    # Parse the XLIFF and convert to XML
    translations = parse_xliff(input_file)
    sorted_translations = sorted(translations.items())
    converted_translations = {}

    for resname, target in sorted_translations:
        converted_translations[resname] = generate_icu_pattern(target)

    # Generate output files
    output_locale = LOCALE_PATH_MAPPING.get(locale, LOCALE_PATH_MAPPING.get(locale_two_letter_code, locale_two_letter_code))
    locale_output_dir = os.path.join(output_dir, output_locale)
    output_file = os.path.join(locale_output_dir, 'messages.json')
    os.makedirs(locale_output_dir, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as file:
        json.dump(converted_translations, file, ensure_ascii=False, indent=2)
        file.write('\n')
        file.write('\n')

def convert_non_translatable_strings_to_type_script(input_file, output_path, rtl_languages):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

    # Process the non-translatable string input
    non_translatable_strings_data = {}
    with open(input_file, 'r') as file:
        non_translatable_strings_data = json.load(file)

    entries = non_translatable_strings_data['data']
    rtl_locales = sorted([lang["twoLettersCode"] for lang in rtl_languages])

    # Output the file in the desired format
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    joined_rtl_locales = {", ".join(f"'{locale}'" for locale in rtl_locales)}

    with open(output_path, 'w', encoding='utf-8') as file:
        file.write('export enum LOCALE_DEFAULTS {\n')
        for entry in entries:
            key = entry['data']['note']
            text = entry['data']['text']
            file.write(f"  {key} = '{text}',\n")

        file.write('}\n')
        file.write('\n')
        file.write(f"export const rtlLocales = [{joined_rtl_locales}] as const;\n")
        file.write('\n')

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
    rtl_languages = [lang for lang in target_languages if lang["textDirection"] == "rtl"]
    convert_non_translatable_strings_to_type_script(non_translatable_strings_file, NON_TRANSLATABLE_STRINGS_OUTPUT_PATH, rtl_languages)
    print(f"\033[2K{Fore.GREEN}✅ Static string generation complete{Style.RESET_ALL}")

    # Convert the XLIFF data to the desired format
    print(f"\033[2K{Fore.WHITE}⏳ Converting translations to target format...{Style.RESET_ALL}", end='\r')
    for language in target_languages:
        lang_locale = language['locale']
        lang_two_letter_code = language['twoLettersCode']
        print(f"\033[2K{Fore.WHITE}⏳ Converting translations for {lang_locale} to target format...{Style.RESET_ALL}", end='\r')
        input_file = os.path.join(input_directory, f"{lang_locale}.xliff")
        convert_xliff_to_json(input_file, TRANSLATIONS_OUTPUT_DIRECTORY, lang_locale, lang_two_letter_code)
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
