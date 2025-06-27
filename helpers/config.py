import json
import os
import pathlib
import sys

import requests

from .singleton import Singleton


class Config(metaclass=Singleton):
    BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
    LOG_DIR = ".log"
    LOG_LOCATION = os.path.join(BASE_DIR, LOG_DIR)
    FAILURES_FILE = "failures.txt"
    FAILURES_LOCATION = os.path.join(LOG_DIR, FAILURES_FILE)
    DEFAULT_CONFIG_FILE = "config.json"
    DEFAULT_CONFIG_LOCATION = os.path.join(BASE_DIR, DEFAULT_CONFIG_FILE)
    ATTACHMENTS_DIR = "attachments"
    REWRITE_DOWNLOAD_URL = True

    def __init__(self, config_file=None, validate=True, asset=False):
        self.config_file = config_file or self.DEFAULT_CONFIG_LOCATION
        self.dest_without_asset_uid = asset
        if validate:
            self._validate_config()
        self.last_failed_uuids = []
        self._create_log_location()
        self._read_failed_transfer_uuids()
        self.src, self.dest = self.get_config()

    def get_config(self):
        data = self._read_config()
        src = self._append_additional_config_data(data["src"])
        dest = self._append_additional_config_data(data["dest"])
        return src, dest

    def update_config(self, loc, new_data={}):
        data = self._read_config()
        setattr(
            self,
            loc,
            self._append_additional_config_data({**data[loc], **new_data}),
        )

    @property
    def data_query(self):
        return {"_uuid": {"$in": self.last_failed_uuids}}

    def _create_log_location(self):
        if not os.path.isdir(self.LOG_LOCATION):
            os.makedirs(self.LOG_LOCATION)

    def _read_failed_transfer_uuids(self):
        if os.path.exists(self.FAILURES_LOCATION):
            with open(self.FAILURES_LOCATION, "r") as f:
                self.last_failed_uuids = f.read().split("\n")[:-1]
            # burn after reading
            os.remove(self.FAILURES_LOCATION)

    def _read_config(self):
        with open(self.config_file, "r") as f:
            config = json.loads(f.read())
        return config

    @staticmethod
    def _append_additional_config_data(data):
        api_v1 = f"{data['kc_url']}/api/v1"
        api_v2 = f"{data['kf_url']}/api/v2"
        assets_url = f"{api_v2}/assets"
        asset_url = f"{api_v2}/assets/{data['asset_uid']}"
        return {
            **data,
            "api_v1": api_v1,
            "api_v2": api_v2,
            "assets_url": assets_url,
            "asset_url": asset_url,
            "asset_url_json": f"{asset_url}.json",
            "submission_url": f"{api_v1}/submissions",
            "forms_url": f"{api_v1}/forms",
            "headers": {"Authorization": f"Token {data['token']}"},
            "params": {"format": "json"},
            "deployment_url": f"{asset_url}/deployment/",
            "xml_url": f"{asset_url}/data.xml",
            "data_url": f"{asset_url}/data",
            "files_url": f"{asset_url}/files",
            "validation_statuses_url": f"{asset_url}/data/validation_statuses.json",
            "advanced_submission_url": f"{data['kf_url']}/advanced_submission_post/{data['asset_uid']}",
        }

    def _validate_config(self):
        print("🕵️ Validating config file")

        def invalid(msg):
            print(msg)
            sys.exit()

        if not os.path.exists(self.config_file):
            invalid(f"⚠️ Config file `{self.config_file}` does not exist.")

        with open(self.config_file, "r") as f:
            try:
                _ = json.loads(f.read())
            except json.decoder.JSONDecodeError:
                invalid(f"⚠️ Could not read `{self.config_file}`.")

        for loc, config in dict(zip(["src", "dest"], self.get_config())).items():
            try:
                # Check KF API endpoint
                kf_res = requests.get(url=config["api_v2"], headers=config["headers"], timeout=10)
                if kf_res.status_code != 200:
                    invalid(
                        f"⚠️ Could not validate `{loc}`. "
                        f"URL `{config['api_v2']}` returned status {kf_res.status_code}. "
                        "Please check `kf_url` and `token` in your config file."
                    )
                
                # Check KC API endpoint
                kc_res = requests.get(url=config["api_v1"], headers=config["headers"], timeout=10)
                if kc_res.status_code != 200:
                    invalid(
                        f"⚠️ Could not validate `{loc}`. "
                        f"URL `{config['api_v1']}` returned status {kc_res.status_code}. "
                        "Please check `kc_url` and `token` in your config file."
                    )

                if not (loc == "dest" and self.dest_without_asset_uid):
                    asset_res = requests.get(
                        url=config["asset_url"],
                        headers=config["headers"],
                        params=config["params"],
                        timeout=10
                    )
                    if asset_res.status_code != 200:
                        invalid(
                            f"⚠️ Could not find asset for `{loc}`. "
                            f"URL `{config['asset_url']}` returned status {asset_res.status_code}. "
                            "Please check `asset_uid` in your config file."
                        )
                    asset_details = asset_res.json()
                    if not asset_details.get("has_deployment"):
                        invalid(f"⚠️ Asset `{config['asset_uid']}` for `{loc}` is not deployed. " "Please deploy and try again.")

            except requests.exceptions.RequestException as e:
                invalid(
                    f"⚠️ A network error occurred while validating `{loc}`. "
                    f"URL: `{e.request.url}`. Error: {e}. "
                    "Please check your network connection and the URLs in your config file."
                )
