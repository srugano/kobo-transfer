import os
import requests
import random
import argparse
from random import choice, randint
from faker import Faker
from datetime import datetime, timedelta
import io
from PIL import Image
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString
from google import genai
from dotenv import load_dotenv

load_dotenv()

try:
    KOBO_API_TOKEN = os.environ["KOBO_API_TOKEN"]
    GOOGLE_API_KEY = os.environ["GEMINI_API_KEY"]
    ASSET_UID = os.environ["ASSET_UID"]
    DATA_API_URL = os.environ["DATA_API_URL"]
except KeyError as e:
    print(f"❌ Error: Environment variable {e} not set.")
    print("Please create a .env file (from .env.example) and set your environment variables.")
    exit()


IMAGE_MODEL = "imagen-3.0-generate-002"

fake = Faker()
try:
    client = genai.Client(api_key=GOOGLE_API_KEY)
except Exception as e:
    print(f"❌ Failed to configure Gemini API: {e}")
    exit()


def generate_image_in_memory(prompt: str) -> io.BytesIO | None:
    print(f"🎨 Generating image for prompt: '{prompt[:60]}...'")
    try:
        response = client.models.generate_images(
            model=IMAGE_MODEL,
            prompt=prompt,
            config=genai.types.GenerateImagesConfig(
                number_of_images=1,
            )
        )
        
        if not response.generated_images:
            print("❌ No image data found in the API response.")
            print(f"   Response: {response}")
            return None

        image_bytes = response.generated_images[0].image.image_bytes
        image = Image.open(io.BytesIO(image_bytes))
        
        img_buffer = io.BytesIO()
        image.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        
        print("✅ Image generated successfully in memory.")
        return img_buffer
        
    except Exception as e:
        print(f"❌ Failed to generate image. Error: {e}")
        if 'response' in locals():
            print(f"   Response: {response}")
        return None

def dict_to_xml_str(submission_dict, asset_uid):
    root = Element('data', id=asset_uid)

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
    
    xml_str = tostring(root, 'utf-8')
    pretty_xml_str = parseString(xml_str).toprettyxml(indent="  ")
    return pretty_xml_str

def create_and_submit_one_form(with_images=False):
    generated_images = {}
    consent_filename = None

    if with_images:
        print("ℹ️ This submission will include images.")
        consent_prompt = "A realistic, high-quality, close-up photo of a simple, generic signature in black ink on a plain white piece of paper. The signature is illegible and looks like a common signature."
        consent_filename = "signature.png"
        
        consent_buffer = generate_image_in_memory(consent_prompt)
        if consent_buffer:
            generated_images[consent_filename] = (consent_filename, consent_buffer, 'image/png')
        else:
            print("⚠️ Warning: Consent image generation failed. Proceeding without images for this submission.")
            with_images = False
            consent_filename = None
    else:
        print("ℹ️ This submission will not include images.")
    
    household_size = randint(1, 5)
    has_alternate = fake.boolean() and household_size > 1
    
    individual_questions_list = []

    for i in range(household_size):
        details = {}
        if i == 0:
            details["relationship_i_c"] = "head"
            age = randint(25, 65)
            details["role_i_c"] = "primary"
        else:
            age = randint(1, 80)
            if age > 18:
                details["relationship_i_c"] = choice(["DAUGHTERINLAW_SONINLAW", "WIFE_HUSBAND", "MOTHER_FATHER", "COUSIN"])
            else:
                details["relationship_i_c"] = "NEPHEW_NIECE"
            
            if i == 1 and has_alternate:
                details["role_i_c"] = "alternate"
            else:
                details["role_i_c"] = "none"

        full_name = fake.name()
        details["full_name_i_c"] = full_name
        details["birth_date_i_c"] = fake.date_of_birth(minimum_age=age, maximum_age=age).strftime("%Y-%m-%d")
        details["estimated_birth_date_i_c"] = "0"
        details["gender_i_c"] = choice(["female", "male"])

        if with_images and (i == 0 or choice([True, False])):
            photo_filename = f"photo_{i}.png"
            prompt = f"A realistic, high-quality, passport-style photo of a {age}-year-old {details['gender_i_c']} person from Burundi named {full_name}. They have a neutral expression and are against a plain, light-colored background. Photorealistic."
            image_buffer = generate_image_in_memory(prompt)
            if image_buffer:
                details["photo_i_c"] = photo_filename
                generated_images[photo_filename] = (photo_filename, image_buffer, 'image/png')

        identification = {}
        id_types = []
        if age >= 18:
            if choice([True, False]):
                id_types.append("national_id")
                identification["national_id_no_i_c"] = fake.ssn()
                identification["national_id_issuer_i_c"] = "BDI"
                
                if with_images and choice([True, False]):
                    id_photo_filename = f"national_id_{i}.png"
                    prompt = f"A realistic, high-quality photo of a fictional Burundi National ID card for '{full_name}'. The card is lying flat on a table. It includes a photo of a Burundian person, placeholder text in French and Kirundi, and a national emblem. All personal details and numbers are fake and illegible."
                    image_buffer = generate_image_in_memory(prompt)
                    if image_buffer:
                        identification["national_id_photo_i_c"] = id_photo_filename
                        generated_images[id_photo_filename] = (id_photo_filename, image_buffer, 'image/png')
        
        identification["id_type_i_c"] = " ".join(id_types) if id_types else "none"

        individual_group_entry = {
            "individual_details": details,
            "identification": identification
        }
        individual_questions_list.append(individual_group_entry)

    consent_data = {
        "consent_sharing_h_c": "unicef humanitarian_partner",
        "consent_h_c": "1",
    }
    if with_images and consent_filename:
        consent_data["consent_sign_h_c"] = consent_filename

    submission_data = {
        "start": datetime.now().isoformat(),
        "end": (datetime.now() + timedelta(minutes=randint(5, 20))).isoformat(),
        "deviceid": fake.pystr(10, 15),
        "registration_method_h_c": "hh_registration",
        "name_enumerator_h_c": fake.name(),
        "org_name_enumerator_h_c": choice(["AVSI", "UNCAF", "Pertinent", "AGEBU", "HELP", "Abigail"]),
        "consent": consent_data,
        "setup": {
            "collect_individual_data_h_c": "1", 
            "currency_h_c": "BIF"
        },
        "household_location": {
            "country_h_c": "BDI",
            "admin1_h_c": choice(["BDI001", "BDI002", "BDI003", "BDI004"]),
            "admin2_h_c": choice(["BDI017016", "BDI017015", "BDI017014", "BDI002004", "BDI003009"]),
            "village_h_c": fake.street_name(),
            "hh_geopoint_h_c": f"{fake.latitude()} {fake.longitude()} 0 0",
        },
        "household_status": {
            "residence_status_h_c": choice(["idp", "host", "non_host", "refugee"]), 
            "size_h_c": str(household_size), 
            "alternate_collector": '1' if has_alternate else '0'
        },
        "income_family_h_f": choice(["1", "0"]),
        "children_6_12": str(randint(0, 3)),
        "score_vulnerability": choice(["0", "1"]),
        "cutoff_vulnerability": "0",
        "number_repeat": str(household_size),
        "number_primary": "1",
        "number_alternate": "1" if has_alternate else "0",
        "individual_questions": individual_questions_list,
    }

    xml_submission_str = dict_to_xml_str(submission_data, ASSET_UID)

    files_to_upload = {
        'xml_submission_file': ('submission.xml', xml_submission_str, 'text/xml'),
        **generated_images
    }

    headers = {"Authorization": f"Token {KOBO_API_TOKEN}"}
    
    print("--- Submitting Form Data (XML) and Generated Images ---")
    
    try:
        response = requests.post(DATA_API_URL, headers=headers, files=files_to_upload)
        response.raise_for_status()

        print("\n✅ Success! Data and images submitted successfully to KoBoToolbox.")
        print(f"Status Code: {response.status_code}")
        print("Response from server:")
        print(response.status_code)

    except requests.exceptions.HTTPError as http_err:
        print(f"\n❌ HTTP error occurred: {http_err}")
        print(f"Status Code: {http_err.response.status_code}")
        print(f"Response Body: {http_err.response.text}")
    except Exception as err:
        print(f"\n❌ An unexpected error occurred: {err}")


if __name__ == "__main__":
    if "your_gemini_api_key" in GOOGLE_API_KEY.lower():
        print("❌ Please replace placeholder API tokens in the .env file before running.")
        exit()

    parser = argparse.ArgumentParser(description="Generate and submit fake KoBoToolbox data.")
    parser.add_argument("-n", "--number", type=int, default=10, help="Number of submissions to create.")
    parser.add_argument("-p", "--image-probability", type=float, default=0.1, help="Probability (0.0 to 1.0) of a submission having images.")
    args = parser.parse_args()

    if not (0.0 <= args.image_probability <= 1.0):
        print("❌ Error: Image probability must be between 0.0 and 1.0.")
        exit()

    for i in range(args.number):
        print(f"\n--- Generating submission {i + 1} of {args.number} ---")
        should_generate_images = random.random() < args.image_probability
        create_and_submit_one_form(with_images=should_generate_images)
