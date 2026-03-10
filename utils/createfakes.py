import os
import requests
import random
import argparse
import uuid
import time
from random import choice, randint
from faker import Faker
from datetime import datetime, timedelta
import io
from PIL import Image
from xml.etree.ElementTree import Element, SubElement, tostring
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from helpers.config import Config

# WARNING: Keep your tokens and keys secure.


IMAGE_DIR = "/home/stock/.cache/kagglehub/datasets/jessicali9530/celeba-dataset/versions/2/img_align_celeba/img_align_celeba/"

fake = Faker()

try:
    image_files = [
        os.path.join(IMAGE_DIR, f)
        for f in os.listdir(IMAGE_DIR)
        if os.path.isfile(os.path.join(IMAGE_DIR, f))
    ]
    if not image_files:
        print(f"❌ Error: No images found in '{IMAGE_DIR}'")
        exit()
except FileNotFoundError:
    print(f"❌ Error: Image directory not found at '{IMAGE_DIR}'")
    print("Please make sure the local image dataset is available.")
    exit()


def generate_image_in_memory() -> io.BytesIO | None:
    """Selects a random image from disk, resizes it, and returns it as an in-memory BytesIO object."""
    if not image_files:
        return None
    try:
        image_path = random.choice(image_files)
        with Image.open(image_path) as img:
            img.thumbnail((400, 400))  # Resize to keep memory usage reasonable
            img_buffer = io.BytesIO()
            img.save(img_buffer, format="JPEG", quality=85)
            img_buffer.seek(0)  # Rewind the buffer to the beginning before returning
            return img_buffer
    except Exception as e:
        tqdm.write(f"❌ Failed to load image from {image_path}. Error: {e}")
        return None


def dict_to_xml_str(submission_dict, asset_uid):
    """Converts a submission dictionary to a KoBo-compatible XML string."""
    root = Element("data", id=asset_uid)

    def build_xml(parent, data):
        for key, value in data.items():
            if isinstance(value, dict):
                group_element = SubElement(parent, key)
                build_xml(group_element, value)
            elif isinstance(value, list):
                for item in value:
                    group_element = SubElement(parent, key)
                    build_xml(group_element, item)
            elif value is not None:
                SubElement(parent, key).text = str(value)

    build_xml(root, submission_dict)

    return tostring(root, "utf-8")


def create_and_submit_one_form(config_dest, with_images=False, throttle=0):
    """Generates and submits a single fake data entry, optionally with images."""
    generated_images = {}

    household_size = randint(1, 5)
    has_alternate = fake.boolean() and household_size > 1

    individual_questions_list = []

    for i in range(household_size):
        details = {}
        # First person is always Head of Household
        if i == 0:
            min_age = 25
            max_age = 65
        else:
            min_age = 1
            max_age = 80

        birth_date = fake.date_of_birth(minimum_age=min_age, maximum_age=max_age)
        age = int((datetime.now().date() - birth_date).days / 365.24)

        if i == 0:
            details["relationship_i_c"] = "head"
            details["role_i_c"] = "primary"
        else:
            if age > 18:
                details["relationship_i_c"] = choice(
                    [
                        "daughterInLaw_sonInLaw",
                        "wife_husband",
                        "mother_father",
                        "cousin",
                    ]
                )
            else:
                details["relationship_i_c"] = "nephew_niece"

            if i == 1 and has_alternate:
                details["role_i_c"] = "alternate"
            else:
                details["role_i_c"] = "no_role"

        first_name = fake.first_name()
        last_name = fake.last_name()
        full_name = f"{first_name} {last_name}"
        details["given_name_i_c"] = first_name
        details["family_name_i_c"] = last_name
        details["full_name_i_c"] = full_name
        details["birth_date_i_c"] = birth_date.strftime("%Y-%m-%d")
        details["estimated_birth_date_i_c"] = "0"
        details["gender_i_c"] = choice(["female", "male"])

        # Always generate for the Head of Household, optional for others
        if with_images and (i == 0 or choice([True, False])):
            photo_filename = f"photo_{i}.jpg"
            image_buffer = generate_image_in_memory()
            if image_buffer:
                details["photo_i_c"] = photo_filename
                generated_images[photo_filename] = (
                    photo_filename,
                    image_buffer,
                    "image/jpeg",
                )

        identification = {}
        id_types = []
        if age >= 18:
            if choice([True, False]):
                id_types.append("national_id")
                identification["national_id_no_i_c"] = fake.ssn()
                identification["national_id_issuer_i_c"] = "AFG"

                if with_images and choice([True, False]):
                    id_photo_filename = f"national_id_{i}.jpg"
                    image_buffer = generate_image_in_memory()
                    if image_buffer:
                        identification["national_id_photo_i_c"] = id_photo_filename
                        generated_images[id_photo_filename] = (
                            id_photo_filename,
                            image_buffer,
                            "image/jpeg",
                        )

        identification["id_type_i_c"] = (
            " ".join(id_types) if id_types else "not_available"
        )

        individual_group_entry = {
            "individual_details": details,
            "identification": identification,
        }
        individual_questions_list.append(individual_group_entry)

    consent_data = {
        "consent_sharing_h_c": "unicef humanitarian_partner",
        "consent_h_c": "1",
        "consent_sign_h_c": "",
    }

    submission_data = {
        "meta": {"instanceID": f"uuid:{uuid.uuid4()}"},
        "start": datetime.now().isoformat(),
        "end": (datetime.now() + timedelta(minutes=randint(5, 20))).isoformat(),
        "deviceid": fake.pystr(10, 15),
        "enumerator": {
            "registration_method_h_c": "hh_registration",
            "collect_individual_data_h_c": "1",
            "currency_h_c": "AFN",
            "name_enumerator_h_c": fake.name(),
            "org_name_enumerator_h_c": choice(
                ["AVSI", "UNCAF", "Pertinent", "AGEBU", "HELP", "Abigail"]
            ),
        },
        "consent": consent_data,
        "household_location": {
            "country_h_c": "AFG",
            "admin1_h_c": choice(["AF01", "AF02", "AF03", "AF04"]),
            "admin2_h_c": choice(["AF0102", "AF0105", "AF0114", "AF0204", "AF0309"]),
            "hh_geopoint_h_c": f"{fake.latitude()} {fake.longitude()} 0 0",
        },
        "household_status": {
            "residence_status_h_c": choice(
                ["idp", "host", "non_host", "others_of_concern"]
            ),
            "size_h_c": str(household_size),
            "alternate_collector": "1" if has_alternate else "0",
        },
        "income_family_h_f": choice(["1", "0"]),
        "children_6_12": str(randint(0, 3)),
        "number_repeat": str(household_size),
        "number_primary": "1",
        "number_alternate": "1" if has_alternate else "0",
        "individual_questions": individual_questions_list,
    }

    xml_submission_str = dict_to_xml_str(submission_data, config_dest["asset_uid"])

    files_to_upload = {
        "xml_submission_file": ("submission.xml", xml_submission_str, "text/xml"),
        **generated_images,
    }

    headers = {"Authorization": f"Token {config_dest['token']}"}

    if throttle > 0:
        time.sleep(throttle)

    try:
        response = requests.post(
            config_dest["submission_url"], headers=headers, files=files_to_upload
        )
        response.raise_for_status()
        return response.status_code

    except requests.exceptions.HTTPError as http_err:
        tqdm.write(f"\n❌ HTTP error occurred: {http_err}")
        tqdm.write(f"Status Code: {http_err.response.status_code}")
        tqdm.write(f"Response Body: {http_err.response.text}")
        return None
    except Exception as err:
        tqdm.write(f"\n❌ An unexpected error occurred: {err}")
        return None


def create_fakes(config_dest, number, image_probability, workers=10, throttle=0):
    """
    Generate and submit a given number of fake submissions concurrently.
    """
    success_count = 0
    failure_count = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                create_and_submit_one_form,
                config_dest,
                random.random() < image_probability,
                throttle,
            )
            for _ in range(number)
        }

        for future in tqdm(
            as_completed(futures), total=number, desc="Submitting fake data"
        ):
            try:
                result = future.result()
                if result is not None and 200 <= result < 300:
                    success_count += 1
                else:
                    failure_count += 1
            except Exception as exc:
                tqdm.write(f"A submission generated an exception: {exc}")
                failure_count += 1

    print("\n--- Summary ---")
    print(f"Total submissions attempted: {number}")
    print(f"✅ Successful submissions: {success_count}")
    print(f"❌ Failed submissions: {failure_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate and submit fake KoBoToolbox data."
    )
    parser.add_argument(
        "-n", "--number", type=int, default=10, help="Number of submissions to create."
    )
    parser.add_argument(
        "-p",
        "--image-probability",
        type=float,
        default=0.1,
        help="Probability (0.0 to 1.0) of a submission having images.",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=0,
        help="Seconds to wait between each submission request to avoid rate-limiting.",
    )
    args = parser.parse_args()

    if not (0.0 <= args.image_probability <= 1.0):
        print("❌ Error: Image probability must be between 0.0 and 1.0.")
        exit()

    try:
        config_dest = Config(validate=False).dest
        create_fakes(
            config_dest=config_dest,
            number=args.number,
            image_probability=args.image_probability,
            throttle=args.throttle,
        )
    except KeyError as e:
        print(f"❌ Error: Could not find configuration key: {e}")
        print("Please check your config.json.")
        exit()
