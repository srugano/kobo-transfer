import glob
import io
import os
import uuid
from xml.etree import ElementTree as ET
import asyncio
import httpx
import requests
from utils.text import get_valid_filename
from helpers.config import Config


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


async def async_submit_data(client, xml_sub, _uuid, original_uuid, xml_value_media_map, config):
    file_tuple = (_uuid, xml_sub)
    files = {"xml_submission_file": file_tuple}

    submission_attachments_path = os.path.join(
        Config.ATTACHMENTS_DIR, config["src_asset_uid"], original_uuid, "*"
    )
    
    opened_files = []
    for file_path in glob.glob(submission_attachments_path):
        filename = os.path.basename(file_path)
        filename_value = xml_value_media_map.get(filename)
        if filename_value:
            f = open(file_path, "rb")
            opened_files.append(f)
            files[filename_value] = (filename_value, f)

    try:
        max_retries = 5
        backoff_factor = 1

        for attempt in range(max_retries):
            try:
                res = await client.post(
                    url=config["dest_url"],
                    files=files,
                    headers=config["dest_headers"],
                )
                if res.status_code not in [429, 500, 502, 503, 504]:
                    return res.status_code
            except (httpx.RequestError, httpx.TimeoutException) as e:
                if attempt == max_retries - 1:
                    return 0 # Fallback failure

            # Exponential backoff
            await asyncio.sleep(backoff_factor * (2 ** attempt))

        return 500
    finally:
        for f in opened_files:
            f.close()


async def async_process_single_submission(client, semaphore, submission_xml, asset_data, quiet, regenerate):
    async with semaphore:
        config_obj = Config()
        messages = []
        try:
            original_uuid = submission_xml.find("meta/instanceID").text.replace("uuid:", "")
        except AttributeError:
            original_uuid = ""
            messages.append("`instanceID` was missing in submission XML")

        if regenerate or not original_uuid:
            _uuid, formatted_uuid = generate_new_instance_id()
            update_element_value(submission_xml, "meta/instanceID", formatted_uuid)
        else:
            _uuid = original_uuid

        new_attrib = {
            "id": asset_data["asset_uid"],
            "version": asset_data["version"],
        }
        update_root_element_tag_and_attrib(submission_xml, asset_data["asset_uid"], new_attrib)
        update_element_value(submission_xml, "__version__", asset_data["__version__"])
        update_element_value(submission_xml, "formhub/uuid", asset_data["formhub_uuid"])

        for el in submission_xml.iter('phone_no_alternative_i_c'):
            if el.text == '+95':
                el.text = None

        for el in submission_xml.iter('phone_no_i_c'):
            if el.text == '+95':
                el.text = None

        primary_name = None
        for details in submission_xml.findall('.//individual_questions'):
            role = details.find('.//role_i_c')
            if role is not None and role.text == 'primary':
                name = details.find('.//full_name_i_c')
                if name is not None and name.text:
                    primary_name = name.text.strip()
                    break

        if primary_name:
            removed_count = 0
            for parent in list(submission_xml.iter()):
                for details in parent.findall('individual_questions'):
                    role = details.find('.//role_i_c')
                    if role is not None and role.text != 'primary':
                        name = details.find('.//full_name_i_c')
                        if name is not None and name.text and name.text.strip() == primary_name:
                            parent.remove(details)
                            removed_count += 1

            if removed_count > 0:
                current_individuals = len(list(submission_xml.iter('individual_questions')))
                old_count_str = str(current_individuals + removed_count)
                new_count_str = str(current_individuals)
                
                for el in submission_xml.iter():
                    if el.text == old_count_str:
                        tag_lower = el.tag.lower()
                        # Update any field that looks like a count or references the repeat group
                        if any(keyword in tag_lower for keyword in ['count', 'num', 'individual', 'total', 'repeat']):
                            el.text = new_count_str

                # Re-index remaining individuals so there are no blank lines or gaps
                index = 1
                for details in submission_xml.findall('.//individual_questions'):
                    idx_node = details.find('.//individual_index')
                    if idx_node is not None:
                        idx_node.text = str(index)
                    index += 1

        submission_values = get_all_values_from_xml(submission_xml)
        xml_value_media_map = get_xml_value_media_mapping(submission_values)

        xml_sub = ET.tostring(submission_xml)
        
        req_config = {
            "src_asset_uid": config_obj.src["asset_uid"],
            "dest_url": config_obj.dest["submission_url"],
            "dest_headers": config_obj.dest["headers"],
        }

        result = await async_submit_data(
            client, xml_sub, _uuid, original_uuid, xml_value_media_map, req_config
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
            
        return result


async def async_transfer_submissions(all_submissions_xml, asset_data, quiet, regenerate, workers):
    semaphore = asyncio.Semaphore(workers)
    limits = httpx.Limits(max_keepalive_connections=workers, max_connections=workers)
    
    async with httpx.AsyncClient(limits=limits, timeout=60.0) as client:
        tasks = [
            async_process_single_submission(
                client, semaphore, submission_xml, asset_data, quiet, regenerate
            )
            for submission_xml in all_submissions_xml
        ]
        results = await asyncio.gather(*tasks)
        return list(results)


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


def transfer_submissions(all_submissions_xml, asset_data, quiet, regenerate, workers=10):
    return asyncio.run(
        async_transfer_submissions(all_submissions_xml, asset_data, quiet, regenerate, workers)
    )


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
