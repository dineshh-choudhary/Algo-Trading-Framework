#Reference: https: // gitlab.com/algo2t/kiteext

import json
import kiteconnect.exceptions as ex
import logging
import pyotp
import requests
import urllib.parse
from exceptions.broker_exceptions import BrokerAuthError, BrokerTOTPError, BrokerError
from kiteconnect import KiteConnect, KiteTicker
from urllib.parse import urljoin
from utils.utils import Utils

class KiteExt(KiteConnect):
    @staticmethod
    def totp(qrcode: str) -> str:
        return f"{int(pyotp.TOTP(qrcode).now()):06d}"

    def login_with_credentials(self, userid: str, password: str, secret: str) -> None:
        self.user_id = userid
        self.password = password
        if (self.user_id == None) or (self.password == None):
            raise BrokerAuthError("Please provide the valid username and password")
        
        if len(secret) == 32:
            try:
                self.twofa = KiteExt.totp(secret)
            except Exception as e:
                raise BrokerTOTPError("TOTP Secret Key contains Non-base32 digit")
        else:
            raise BrokerTOTPError("Incorrect TOTP BASE32 Secret Key")
        
        self.reqsession = requests.Session()
        response = self.reqsession.post(
            self.root + self._routes.get("api.login"),
            data={"user_id": self.user_id, "password": self.password},
        )
        if response.status_code != 200:
            raise BrokerAuthError(response.json().get('message').rstrip('.'))

        response = self.reqsession.post(
            self.root + self._routes["api.twofa"],
            data={
                "user_id": response.json().get("data").get("user_id"),
                "request_id": response.json().get("data").get("request_id"),
                "twofa_value": self.twofa,
                "skip_session": "true",
            },
        )
        if response.status_code != 200:
            raise BrokerTOTPError(response.json().get('message'))
        
        self.enctoken = response.cookies.get("enctoken")
        self.public_token = response.cookies.get("public_token")
        self.user_id = response.cookies.get("user_id")
        self.headers["Authorization"] = "enctoken {}".format(self.enctoken)

    def login_using_enctoken(
        self, userid: str, enctoken: str, public_token: str = None
    ) -> None:
        self.user_id = userid
        self.reqsession = requests.Session()
        self.enctoken = enctoken
        self.public_token = public_token
        # self.user_id = r.cookies.get('user_id')
        self.headers["Authorization"] = "enctoken {}".format(self.enctoken)

    def __init__(
        self, api_key: str = "kitefront", userid: str = None, *args, **kw
    ) -> None:
        KiteConnect.__init__(self, api_key=api_key, *args, **kw)
        if userid is not None:
            self.user_id = userid
        self.user_agent = requests.get(
            "https://techfanetechnologies.github.io"
            + "/latest-user-agent/user_agents.json"
        ).json()[-2]
        self.headers = {
            "x-kite-version": "3.0.9",
            "User-Agent": self.user_agent,
        }
        self._routes.update(
            {
                "api.login": "/api/login",
                "api.twofa": "/api/twofa",
                "api.misdata": "/margins/equity",
            }
        )

    def set_headers(self, enctoken: str, userid: str = None) -> None:
        self.public_token = enctoken
        self.enctoken = enctoken
        if userid is not None:
            self.user_id = userid
        else:
            raise BrokerError(
                "userid field cannot be none, "
                + "either login with credentials "
                + "first or set userid here"
            )
        self.headers["Authorization"] = "enctoken {}".format(self.enctoken)

    def kws(self, api_key="kitefront"):
        return KiteTicker(
            api_key=api_key,
            access_token="&user_id="
            + self.user_id
            + "&enctoken="
            + urllib.parse.quote(self.enctoken)
            + "&user-agent=kite3-web&version=3.0.9",
            root="wss://ws.zerodha.com",
        )

    def ticker(self, api_key="kitefront", enctoken=None, userid=None):
        if enctoken is not None:
            self.enctoken = enctoken
        if userid is not None:
            self.user_id = userid
        if self.user_id is None:
            raise Exception(
                f"userid cannot be none, either login with credentials first or set userid here"
            )
        return KiteTicker(
            api_key=api_key,
            access_token="&user_id="
            + self.user_id
            + "&enctoken="
            + urllib.parse.quote(self.enctoken)
            + "&user-agent=kite3-web&version=3.0.9",
            root="wss://ws.zerodha.com",
        )

    # NOTE NEW
    def _request(
        self,
        route,
        method,
        url_args=None,
        params=None,
        is_json=False,
        query_params=None,
    ):  # noqa: E501
        """Make an HTTP request."""
        # Form a restful URL
        if url_args:
            uri = self._routes[route].format(**url_args)
        else:
            uri = self._routes[route]
        url = urljoin(self.root, uri)

        headers = self.headers

        if self.debug:
            logging.debug(
                "Request: {method} {url} {params} {headers}".format(
                    method=method, url=url, params=params, headers=headers
                )
            )
        # prepare url query params
        if method in ["GET", "DELETE"]:
            query_params = params
        try:
            r = self.reqsession.request(
                method,
                url,
                json=params if (method in ["POST", "PUT"] and is_json) else None,
                data=params if (method in ["POST", "PUT"] and not is_json) else None,
                params=params if method in ["GET", "DELETE"] else None,
                headers=headers,
                verify=not self.disable_ssl,
                allow_redirects=True,
                timeout=self.timeout,
                proxies=self.proxies,
            )

        except Exception as e:
            raise e
        if self.debug:
            logging.debug(
                "Response: {code} {content}".format(
                    code=r.status_code, content=r.content
                )
            )
        # Validate the content type.
        if "json" in r.headers["content-type"]:
            try:
                data = json.loads(r.content.decode("utf8"))
            except ValueError:
                raise ex.DataException(
                    "Could not parse the JSON response received from the server: {content}".format(
                        content=r.content
                    )
                )
            # api error
            if data.get("error_type"):
                # Call session hook if its registered and TokenException is
                # raised
                if (
                    self.session_expiry_hook
                    and r.status_code == 403
                    and data["error_type"] == "TokenException"
                ):
                    self.session_expiry_hook()
                # native Kite errors
                exp = getattr(ex, data["error_type"], ex.GeneralException)
                raise exp(data["message"], code=r.status_code)
            return data["data"]
        elif "csv" in r.headers["content-type"]:
            return r.content
        else:
            raise ex.DataException(
                "Unknown Content-Type ({content_type}) with response: ({content})".format(
                    content_type=r.headers["content-type"], content=r.content
                )
            )

    def login_url(self):
        """Get the remote login url to which a user should be redirected to initiate the login flow."""
        return Utils.get_external_url('login_broker_api')