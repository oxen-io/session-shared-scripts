import os
import json
import xml.etree.ElementTree as ET
import sys
import argparse
import html
from pathlib import Path
from colorama import Fore, Style, init
from datetime import datetime

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Convert a XLIFF translation files to Apple String Catalog.')
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

    file_elem = root.find('ns:file', namespaces=namespace)
    if file_elem is None:
        raise ValueError(f"Invalid XLIFF structure in file: {file_path}")

    target_language = file_elem.get('target-language')
    if target_language is None:
        raise ValueError(f"Missing target-language in file: {file_path}")

    for trans_unit in root.findall('.//ns:trans-unit', namespaces=namespace):
        resname = trans_unit.get('resname') or trans_unit.get('id')
        if resname is None:
            continue  # Skip entries without a resname or id

        target = trans_unit.find('ns:target', namespaces=namespace)
        source = trans_unit.find('ns:source', namespaces=namespace)

        if target is not None and target.text:
            translations[resname] = target.text
        elif source is not None and source.text:
            # If target is missing or empty, use source as a fallback
            translations[resname] = source.text
            print(f"Warning: Using source text for '{resname}' as target is missing or empty")

    # Handle plural groups
    for group in root.findall('.//ns:group[@restype="x-gettext-plurals"]', namespaces=namespace):
        plural_forms = {}
        resname = None
        for trans_unit in group.findall('ns:trans-unit', namespaces=namespace):
            if resname is None:
                resname = trans_unit.get('resname') or trans_unit.get('id')
            target = trans_unit.find('ns:target', namespaces=namespace)
            context_group = trans_unit.find('ns:context-group', namespaces=namespace)
            if context_group is not None:
                plural_form = context_group.find('ns:context[@context-type="x-plural-form"]', namespaces=namespace)
                if target is not None and target.text and plural_form is not None:
                    form = plural_form.text.split(':')[-1].strip().lower()
                    plural_forms[form] = target.text
        if resname and plural_forms:
            translations[resname] = plural_forms

    return translations, target_language

def clean_string(text):
    # Note: any changes done for all platforms needs most likely to be done on crowdin side.
    # So we don't want to replace -&gt; with → for instance, we want the crowdin strings to not have those at all.
    text = html.unescape(text)          # Unescape any HTML escaping
    return text.strip()                 # Strip whitespace

def convert_placeholders_for_plurals(resname, translations):
    # Replace {count} with %lld for iOS
    converted_translations = {}
    for form, value in translations.items():
        converted_translations[form] = clean_string(value.replace('{count}', '%lld'))

    return converted_translations

def convert_xliff_to_string_catalog(input_dir, output_dir, source_language, target_languages):
    string_catalog = {
        "sourceLanguage": "en",
        "strings": {},
        "version": "1.0"
    }

    for language in [source_language] + target_languages:
        lang_locale = language['locale']
        input_file = os.path.join(input_dir, f"{lang_locale}.xliff")

        if not os.path.exists(input_file):
            raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

        try:
            translations, target_language = parse_xliff(input_file)
        except Exception as e:
            raise ValueError(f"Error processing file {filename}: {str(e)}")

        print(f"\033[2K{Fore.WHITE}⏳ Converting translations for {target_language} to target format...{Style.RESET_ALL}", end='\r')
        sorted_translations = sorted(translations.items())

        for resname, translation in sorted_translations:
            if resname not in string_catalog["strings"]:
                string_catalog["strings"][resname] = {
                    "extractionState": "manual",
                    "localizations": {}
                }

            if isinstance(translation, dict):  # It's a plural group
                converted_translations = convert_placeholders_for_plurals(resname, translation)

                # Check if any of the translations contain '{count}'
                contains_count = any('{count}' in value for value in translation.values())

                if contains_count:
                    # It's a standard plural which the code can switch off of using `{count}`
                    variations = {
                        "plural": {
                            form: {
                                "stringUnit": {
                                    "state": "translated",
                                    "value": value
                                }
                            } for form, value in converted_translations.items()
                        }
                    }
                    string_catalog["strings"][resname]["localizations"][target_language] = {"variations": variations}
                else:
                    # Otherwise we need to use a custom format which uses just the `{count}` and replaces it with an entire string
                    string_catalog["strings"][resname]["localizations"][target_language] = {
                        "stringUnit": {
                            "state": "translated",
                            "value": "%#@arg1@"
                        },
                        "substitutions": {
                            "arg1": {
                                "argNum": 1,
                                "formatSpecifier": "lld",
                                "variations": {
                                    "plural": {
                                        form: {
                                            "stringUnit": {
                                                "state": "translated",
                                                "value": value
                                            }
                                        } for form, value in converted_translations.items()
                                    }
                                }
                            }
                        }
                    }
            else:
                string_catalog["strings"][resname]["localizations"][target_language] = {
                    "stringUnit": {
                        "state": "translated",
                        "value": clean_string(translation)
                    }
                }

    output_file = os.path.join(output_dir, 'Localizable.xcstrings')
    os.makedirs(output_dir, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(string_catalog, f, ensure_ascii=False, indent=2)

def convert_non_translatable_strings_to_swift(input_file, output_path):
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Could not find '{input_file}' in raw translations directory")

    # Process the non-translatable string input
    non_translatable_strings_data = {}
    with open(input_file, 'r', encoding="utf-8") as file:
        non_translatable_strings_data = json.load(file)

    entries = non_translatable_strings_data['data']

    # Output the file in the desired format
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as file:
        file.write(f'// Copyright © {datetime.now().year} Rangeproof Pty Ltd. All rights reserved.\n')
        file.write('// This file is automatically generated and maintained, do not manually edit it.\n')
        file.write('//\n')
        file.write('// stringlint:disable\n')
        file.write('\n')
        file.write('public enum Constants {\n')
        for entry in entries:
            key = entry['data']['note']
            text = entry['data']['text']
            file.write(f'    public static let {key}: String = "{text}"\n')

        file.write('}\n')
        file.write('\n')

def convert_all_files(input_directory):
    # Extract the project information
    print(f"\033[2K{Fore.WHITE}⏳ Processing project info...{Style.RESET_ALL}", end='\r')
    project_info_file = os.path.join(input_directory, "_project_info.json")
    if not os.path.exists(project_info_file):
        raise FileNotFoundError(f"Could not find '{project_info_file}' in raw translations directory")

    project_details = {}
    with open(project_info_file, 'r', encoding="utf-8") as file:
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
    convert_non_translatable_strings_to_swift(non_translatable_strings_file, NON_TRANSLATABLE_STRINGS_OUTPUT_PATH)
    print(f"\033[2K{Fore.GREEN}✅ Static string generation complete{Style.RESET_ALL}")

    # Convert the XLIFF data to the desired format
    print(f"\033[2K{Fore.WHITE}⏳ Converting translations to target format...{Style.RESET_ALL}", end='\r')
    convert_xliff_to_string_catalog(input_directory, TRANSLATIONS_OUTPUT_DIRECTORY, source_language, target_languages)
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
