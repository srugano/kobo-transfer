import glob
import io
import json
import os
import uuid
from datetime import datetime
from xml.etree import ElementTree as ET

import requests

from .media import get_media, del_media
from utils.text import get_valid_filename
from helpers.config import Config

from .media import del_media, get_media


def get_submission_edit_data():
    config = Config().dest
    _v_, v = get_info_from_deployed_versions()
    data = {
        "asset_uid": config["asset_uid"],
        "version": v,
        "__version__": _v_,
        "formhub_uuid": get_formhub_uuid(),
    }
    return data


def get_all_values_from_xml(elem):
    """
    Return a list of all the values in the submission's XML
    """
    values = []
    for child in elem:
        values.extend(get_all_values_from_xml(child))
    if elem.text:
        values.append(elem.text.strip())
    return [v for v in values if v]


def get_xml_value_media_mapping(values):
    """
    Return a mapping of the filename as it's stored and the submission value
    as it's sent. We need this to link the two together again for when filenames
    are stripped of special characters.
    """
    return {get_valid_filename(v): v for v in values}


def get_src_submissions_xml(xml_url):
    config = Config().src
    res = requests.get(url=xml_url, headers=config["headers"])
    if not res.status_code == 200:
        raise Exception("Something went wrong")
    return ET.fromstring(res.text)


def submit_data(xml_sub, _uuid, original_uuid, xml_value_media_map):
    config = Config().dest

    file_tuple = (_uuid, io.BytesIO(xml_sub))
    files = {"xml_submission_file": file_tuple}

    # see if there is media to upload with it
    submission_attachments_path = os.path.join(Config.ATTACHMENTS_DIR, Config().src["asset_uid"], original_uuid, "*")
    for file_path in glob.glob(submission_attachments_path):
        filename = os.path.basename(file_path)
        filename_value = xml_value_media_map.get(filename)
        files[filename_value] = (filename_value, open(file_path, "rb"))

    res = requests.Request(
        method="POST",
        url=config["submission_url"],
        files=files,
        headers=config["headers"],
    )
    session = requests.Session()
    res = session.send(res.prepare())
    return res.status_code


def update_element_value(e, path, value):
    """
    Get or create a node and give it a value, even if nested within a group.
    """
    parts = path.split("/")
    if len(parts) == 1:
        el = e.find(parts[0])
        if el is None:
            el = ET.SubElement(e, parts[0])
        el.text = value
    else:
        parent = e.find(parts[0])
        if parent is None:
            parent = ET.SubElement(e, parts[0])
        update_element_value(parent, "/".join(parts[1:]), value)


def update_root_element_tag_and_attrib(e, tag, attrib):
    """
    Update the root of each submission's XML tree
    """
    e.tag = tag
    e.attrib = attrib


def generate_new_instance_id() -> (str, str):
    """
    Returns:
        - Generated uuid
        - Formatted uuid for OpenRosa xml
    """
    _uuid = str(uuid.uuid4())
    return _uuid, f"uuid:{_uuid}"


def _add_financial_info(target_repeat_group, source_financial_group, source_ewallet_group=None):
    """
    Maps financial information from a source group to the destination structure
    within a target individual_questions repeat group.
    """
    if source_financial_group is None:
        return

    if source_ewallet_group is None:
        source_ewallet_group = source_financial_group

    # Determine prefixes and suffixes for source field names
    tag = source_financial_group.tag
    is_proxy = tag == 'proxy_financial_information'
    
    p_suffix = '_proxy' if is_proxy else ''

    # --- Bank Info ---
    if source_financial_group.findtext(f'bank_account_h_f{p_suffix}') == '1':
        bank_mappings = {
            f'bank_account_h_f{p_suffix}': 'financial_information/bank_account_h_f',
            f'bank_names_h_f{p_suffix}': 'financial_information/bank_names_h_f',
            f'branch_names_h_f{p_suffix}': 'financial_information/bank_branch_name',
            f'bank_account_number_h_f{p_suffix}': 'financial_information/bank_account_number_h_f',
            f'bank_account_number{p_suffix}': 'financial_information/bank_account_number',
            f'bank_given_name{p_suffix}': 'financial_information/bank_given_name',
            f'bank_father_name{p_suffix}': 'financial_information/bank_father_name',
            f'bank_grandfather_name{p_suffix}': 'financial_information/bank_grandfather_name',
            f'bank_family_name{p_suffix}': 'financial_information/bank_family_name',
            f'bank_proof_h_f{p_suffix}': 'financial_information/bank_proof_h_f',
            f'bankapp_account_app_h_f{p_suffix}': 'financial_information/bank_account_app_h_f',
            f'bankapp_app_name_h_f{p_suffix}': 'financial_information/bank_account_app_name_h_f',
        }

        for src_tag, dest_path in bank_mappings.items():
            element = source_financial_group.find(src_tag)
            if element is not None and element.text:
                update_element_value(target_repeat_group, dest_path, element.text)
        
        specify_val = source_financial_group.findtext(f'bank_names_h_f{p_suffix}_specify')
        if specify_val:
            update_element_value(target_repeat_group, 'financial_information/bank_branch_name', specify_val)
        
        other_app_val = source_financial_group.findtext(f'bankapp_other_indicate{p_suffix}')
        if other_app_val:
            update_element_value(target_repeat_group, 'financial_information/bank_account_app_name_h_f', other_app_val)

    # --- E-Wallet Info ---
    if source_ewallet_group.findtext(f'e_wallet{p_suffix}') == '1':
        ewallet_mappings = {
            f'e_wallet{p_suffix}': 'financial_information/e_wallet',
            f'e_wallet_app_name_h_f{p_suffix}': 'financial_information/e_wallet_app_name_h_f',
            f'e_wallet_other_indicate{p_suffix}': 'financial_information/account_mobile_service_provide',
            f'e_walletphone_no_1{p_suffix}': 'financial_information/e_wallet_account_number_h_f',
            f'e_walletphone_no{p_suffix}': 'financial_information/account_mobile_mobile_number_c',
        }

        for src_tag, dest_path in ewallet_mappings.items():
            element = source_ewallet_group.find(src_tag)
            if element is not None and element.text:
                update_element_value(target_repeat_group, dest_path, element.text)

        # Handle concatenated name
        given = source_ewallet_group.findtext(f'e_wallet_given_name{p_suffix}')
        father = source_ewallet_group.findtext(f'e_wallet_father_name{p_suffix}')
        grand = source_ewallet_group.findtext(f'e_wallet_grandfather_name{p_suffix}')
        family = source_ewallet_group.findtext(f'e_wallet_family_name{p_suffix}')
        full_name_parts = [name for name in [given, father, grand, family] if name]
        if full_name_parts:
            full_name = " ".join(full_name_parts)
            update_element_value(target_repeat_group, 'financial_information/e_wallet_account_holder_name_i', full_name)


def transform_submission_xml(source_xml, asset_data):
    """
    Transforms a source submission XML into the new structure required by the destination form.
    """
    # Create the root of the new XML for the destination asset
    new_root = ET.Element('data', id=asset_data["asset_uid"])

    # --- Step 1: Handle Primary Beneficiary ---
    primary_details_src = source_xml.find('primary_individual_details')
    primary_id_src = source_xml.find('primary_identification')
    primary_contact_info_src = source_xml.find('primary_contact_information')
    
    primary_individual_repeat = None
    has_primary_financial_info = False
    if primary_details_src is not None:
        primary_individual_repeat = ET.SubElement(new_root, 'individual_questions')
        
        primary_details_src.tag = 'individual_details'
        primary_individual_repeat.append(primary_details_src)
        
        if primary_contact_info_src is not None:
            primary_contact_info_src.tag = 'contact_information'
            primary_individual_repeat.append(primary_contact_info_src)
        if primary_id_src is not None:
            primary_id_src.tag = 'identification'
            primary_individual_repeat.append(primary_id_src)

        update_element_value(primary_individual_repeat, 'individual_details/relationship_i_c', 'head')
        update_element_value(primary_individual_repeat, 'individual_details/role_i_c', 'primary')
        
        dest_primary_details = primary_individual_repeat.find('individual_details')
        midwife_slip_element = source_xml.find('household_status/midwife_slip_picture')
        if midwife_slip_element is not None and midwife_slip_element.text and dest_primary_details is not None:
            ET.SubElement(dest_primary_details, 'midwife_slip_i_f').text = midwife_slip_element.text

        # Add financial info if it belongs to the primary
        primary_financial_info_src = source_xml.find('Primary_financial_information')
        if primary_financial_info_src is not None and (primary_financial_info_src.findtext('bank_account_h_f') == '1' or primary_financial_info_src.findtext('e_wallet') == '1'):
            has_primary_financial_info = True
            _add_financial_info(primary_individual_repeat, primary_financial_info_src)

    # --- Step 2: Handle Proxy Individual (if they exist) ---
    proxy_bio_src = source_xml.find('proxy_bio')
    has_proxy_individual = proxy_bio_src is not None and proxy_bio_src.findtext('proxy_relationship')
    
    proxy_individual_repeat = None
    if has_proxy_individual:
        proxy_individual_repeat = ET.SubElement(new_root, 'individual_questions')
        update_element_value(proxy_individual_repeat, 'individual_details/role_i_c', 'alternate')

        proxy_bio_mappings = {
            'proxy_relationship': 'individual_details/relationship_i_c', 'given_name_proxy': 'individual_details/given_name_i_c',
            'father_name_proxy': 'individual_details/father_name', 'grandfather_name_proxy': 'individual_details/grandfather_name',
            'family_name_proxy': 'individual_details/family_name_i_c', 'birth_date_proxy': 'individual_details/birth_date_i_c',
            'estimated_birth_date_proxy': 'individual_details/estimated_birth_date_i_c', 'gender_proxy': 'individual_details/gender_i_c',
        }
        for src_tag, dest_path in proxy_bio_mappings.items():
            element = proxy_bio_src.find(src_tag)
            if element is not None and element.text:
                update_element_value(proxy_individual_repeat, dest_path, element.text)

        proxy_id_src = source_xml.find('proxy_identification')
        if proxy_id_src is not None:
            proxy_id_src.tag = 'identification'
            proxy_individual_repeat.append(proxy_id_src)
        proxy_contact_src = source_xml.find('proxy_contact_information_proxy')
        if proxy_contact_src is not None:
            proxy_contact_src.tag = 'contact_information'
            proxy_individual_repeat.append(proxy_contact_src)

    # Add financial info for proxy if primary doesn't have it
    if not has_primary_financial_info:
        proxy_financial_info_src = source_xml.find('proxy_financial_information')
        
        # If there's a proxy individual, attach financial info to them.
        # Otherwise, attach to the primary individual.
        financial_info_target = proxy_individual_repeat if has_proxy_individual else primary_individual_repeat

        if financial_info_target:
            if proxy_financial_info_src is not None and (proxy_financial_info_src.findtext('bank_account_h_f_proxy') == '1' or proxy_financial_info_src.findtext('e_wallet_proxy') == '1'):
                _add_financial_info(financial_info_target, proxy_financial_info_src)

    # --- Step 3: Copy remaining top-level metadata ---
    for tag in ['start', 'end', 'deviceid', 'enumerator', 'consent', 'household_location', 'household_status', 'meta', '__version__', 'formhub']:
        element = source_xml.find(tag)
        if element is not None:
            if tag == 'household_status':
                disability_element_to_remove = element.find('observed_disability_i_c')
                if disability_element_to_remove is not None:
                    element.remove(disability_element_to_remove)
                midwife_slip_to_remove = element.find('midwife_slip_picture')
                if midwife_slip_to_remove is not None:
                    element.remove(midwife_slip_to_remove)
            new_root.append(element)

    # --- Step 4: Create repeat instances for Children ---
    child_mappings = {
        'individual_details/given_name_child': 'individual_details/given_name_i_c',
        'individual_details/father_name_child': 'individual_details/father_name',
        'individual_details/grandfather_name_child': 'individual_details/grandfather_name',
        'individual_details/family_name_child': 'individual_details/family_name_i_c',
        'individual_details/birth_date_child': 'individual_details/birth_date_i_c',
        'individual_details/gender_child': 'individual_details/gender_i_c',
        'individual_details/id_type_child': 'identification/id_type_i_c',
        'individual_details/birth_certificate_no_child': 'identification/birth_certificate_no_i_c',
        'individual_details/birth_certificate_no_child_001': 'identification/birth_certificate_no',
        'individual_details/national_id_no_i_c_child': 'identification/national_id_no_i_c',
        'individual_details/confirm_national_id_no_i_c_child': 'identification/national_id_no',
        'individual_details/national_id_issuer_i_c_child': 'identification/national_id_issuer_i_c',
        'individual_details/national_id_photo_i_c_child': 'identification/national_id_photo_i_c',
        'individual_details/national_id_type_i_f_child': 'identification/national_id_type_i_f',
        'individual_details/birth_certificate_issuer_child': 'identification/birth_certificate_issuer_i_c',
        'individual_details/birth_certificate_photo_child': 'identification/birth_certificate_photo_i_c',
        'individual_details/photo_community_verification_child': 'identification/picture_community_form_h_f',
    }

    source_children = source_xml.findall('individual_questions')
    for child_repeat in source_children:
        individual_repeat = ET.SubElement(new_root, 'individual_questions')
        update_element_value(individual_repeat, 'individual_details/role_i_c', 'no_role')
        update_element_value(individual_repeat, 'individual_details/relationship_i_c', 'son_daughter')

        for source_path, dest_path in child_mappings.items():
            source_element = child_repeat.find(source_path)
            if source_element is not None and source_element.text is not None:
                update_element_value(individual_repeat, dest_path, source_element.text)

    return new_root


def transfer_submissions(all_submissions_xml, asset_data, quiet, regenerate):
    results = []
    for submission_xml in all_submissions_xml:
        messages = []
        try:
            original_uuid = submission_xml.find("meta/instanceID").text.replace("uuid:", "")
        except AttributeError:
            original_uuid = ""
            messages.append("`instanceID` was missing in submission XML")

        if regenerate or not original_uuid:
            _uuid, formatted_uuid = generate_new_instance_id()
        else:
            _uuid = original_uuid
            formatted_uuid = f"uuid:{_uuid}"

        new_submission_xml = transform_submission_xml(submission_xml, asset_data)

        update_element_value(new_submission_xml, "meta/instanceID", formatted_uuid)
        update_root_element_tag_and_attrib(new_submission_xml, asset_data["asset_uid"], {"id": asset_data["asset_uid"], "version": asset_data["version"]})
        update_element_value(new_submission_xml, "__version__", asset_data["__version__"])
        update_element_value(new_submission_xml, "formhub/uuid", asset_data["formhub_uuid"])

        submission_values = get_all_values_from_xml(new_submission_xml)
        xml_value_media_map = get_xml_value_media_mapping(submission_values)

        result = submit_data(
            ET.tostring(new_submission_xml),
            _uuid,
            original_uuid,
            xml_value_media_map,
        )
        if result == 201:
            messages.append(f"✅ {_uuid}")
        elif result == 202:
            messages.append(f"⚠️  {_uuid}")
        else:
            messages.append(f"❌ {_uuid}")
            log_failure(_uuid)
        if not quiet:
            print(" | ".join(reversed(messages)))
        results.append(result)
    return results


def log_failure(_uuid):
    with open(Config.FAILURES_LOCATION, "a") as f:
        f.write(f"{_uuid}\n")


def get_formhub_uuid():
    config = Config().dest
    res = requests.get(
        url=config["forms_url"],
        headers=config["headers"],
        params=config["params"],
    )
    if not res.status_code == 200:
        raise Exception("Something went wrong")
    all_forms = res.json()
    latest_form = [f for f in all_forms if f["id_string"] == config["asset_uid"]][0]
    return latest_form["uuid"]


def get_deployed_versions():
    config = Config().dest
    res = requests.get(
        url=config["asset_url"],
        headers=config["headers"],
        params=config["params"],
    )
    if not res.status_code == 200:
        raise Exception("Something went wrong")
    data = res.json()
    return data["deployed_versions"]


def format_date_string(date_str):
    """
    Format goal: "1 (2021-03-29 19:40:28)"
    """
    date, time = date_str.split("T")
    return f"{date} {time.split('.')[0]}"


def get_info_from_deployed_versions():
    """
    Get the version formats
    """
    deployed_versions = get_deployed_versions()
    count = deployed_versions["count"]

    latest_deployment = deployed_versions["results"][0]
    date = latest_deployment["date_deployed"]
    version = latest_deployment["uid"]

    return version, f"{count} ({format_date_string(date)})"


def print_stats(results):
    total = len(results)
    success = results.count(201)
    skip = results.count(202)
    fail = total - success - skip
    print(f"🧮 {total}\t✅ {success}\t⚠️ {skip}\t❌ {fail}")
