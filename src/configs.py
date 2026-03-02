import requests

url = "https://music.amazon.co.jp/config.json"


REQUIRED_ORDER = [
    "am-loader-experiment",
    "cwr_u",
    "session-id",
    "ubid-acbjp",
    "lc-acbjp",
    "sso-state-acbjp",
    "x-acbjp",
    "at-acbjp",
    "sess-at-acbjp",
    "sst-acbjp",
    "session-id-time",
    "am-token",
    "cwr_s",
    "session-token",
]

class Configs:

    @staticmethod
    def fetch_configs(cookieHeader: str):
        headers = {
            "Cookie": cookieHeader,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        }

        response = requests.get(
            url,
            headers=headers
        )

        if response.status_code == 200:
            data = response.json()  # Parse JSON into Python dict
            
            # Access top-level fields
            # print("Field example:", data["field_name"])
            # Safely access (prevents KeyError)
            # print("Safe access:", data.get("field_name"))
            # Access nested fields
            # print("Nested value:", data["parent"]["child"])
            # Access list items
            # print("First item:", data["items"][0])
            # print("Item property:", data["items"][0]["name"])
            return data
        else:
            print("Error:", response.text)

# am-loader-experiment=7SBEQKWXH86XAWZWAN47;
#  V cwr_u=b1c40e55-b179-42c3-8249-e7980b027571;
#  V session-id=357-9334536-5583055;
# ubid-acbjp=358-3011604-1694212;
# lc-acbjp=ja_JP;
# sso-state-acbjp=Xdsso|ZQFkVdKx9Pr702GW7pXrT-p6ZDwj2i9nUaTyWM0N86pz44DTN2KxI_Ebgyu3MawsEKYOXhVXggCx1UQY1HM0akUNjPdT-5EZim_xVHh7sP8kefYA;
# x-acbjp=e?lp4R33IUTj@Zavy2xosJK5NWphT7NEX?WmqTehcs9FwxNvtwP7?OTymQxUsm0y;
# at-acbjp=Atza|gQC4RcqsAQEBAb2o-lV5UV7KALZ02CGjtEcv_zVzBtqZs1Zn2QjJTzqdBYF2nFvROEgV641lphKBER5wcBbUQTJvSfziycszFyoQPbcrTbPbBPG0mVtDPapWiOXPDcOLSdkgbK2UvxC7uRqqTjNf5-QzxmU76fY1dJJCZEBdO_ALyqkQUcMm5m1fcTRO5AbzR1qugyoDNa3pzC1Mx9WmXng4kDX9wLAgwBScKr7qvnkELTWK74jfRdh3zo9dxIa7WNG6ms2UMX0a4iRAmDo7a33GrMygpRL2JQXM8UbPMzXdMs8BzvnEv9PVvRArvPvPTu9jaI6PtCPTBOiSSLkOT3DsQmWybn6Utyq6Wg;
# sess-at-acbjp=CS8pOBYCCgJGMqFt4xo3NVP5YS4nPMdf2cz3UEQaGJ8=;
# sst-acbjp=Sst1|PQK4yED35CpDTgpw7UJukAOzCe4g7RH_87dLM0-rVAGHC9peOqwCoEn-qWRD6MYHdgplguaSPfyfHszwYCW3U19bDaedx3xsLFAS5xkst09OCv83AJgxNs_852U_c4KHd_zsF9SGx0zYkTCFdJWOO7i7070BHaiwWrjZGT6wlZbI-yEifod-I_F3RBGZDJLsA_FMtm_nGKT-ohqNljnPyq7hyofc890rLpCbFcnCA9yl-n_62c_o18MAtdNV9brejc2ieGOal_AwREKCpZPJW_v0I83A0jTjnQJszcmJHtXfAtI;
#  V session-id-time=2082787201l;
# am-token=eyJkZXZpY2VJZCI6IjM1NzkzMzQ1MzY1NTgzMDU1IiwiaGFzQXV0aGVudGljYXRlZCI6dHJ1ZSwicHJvZmlsZUlkIjoiYW16bjEuYWN0b3IucGVyc29uLmRpZC5BTk1ETEpSSlNBWFVVWFc1NlpDS1IyVE5MWEFJSkxQSFozSlBTTjQ3TkhaUktIV0NIVkFYSVYzVlNVR1pFQUZXVkhUWFNOVlkiLCJndWVzdFRva2VuIjpudWxsLCJndWVzdFViaWQiOm51bGwsImd1ZXN0Q29udGV4dCI6bnVsbCwiaGFzQ29tcGxldGVkRGF0YVRyYW5zZmVyIjpmYWxzZX0%3D;
#  V  cwr_s=eyJzZXNzaW9uSWQiOiJkMjNkYmE2NC00NjVjLTRkYmQtYjlkOS1kMTFjYzYyZWMzNDMiLCJyZWNvcmQiOnRydWUsImV2ZW50Q291bnQiOjgyLCJwYWdlIjp7InBhZ2VJZCI6ImZpbmQiLCJpbnRlcmFjdGlvbiI6MCwicmVmZXJyZXIiOiIiLCJyZWZlcnJlckRvbWFpbiI6IiIsInN0YXJ0IjoxNzcyMzkwOTQxMTc2fX0=;
#  V session-token=kIZE9aN5W2K9nIesdTIVieAVIbgiCX1%2FoRtwXbB9KPOo7QGgSr8FN9qGbd9py4PYmdA8jPbJtJNPBmdrIOsT8tS0lEkGdPByFW3Y3fyoxLWq8f818Z2M4pKA%2BXkzW5ChQhdXi9mZreLd1TUXy2ncJHgM%2BUtOraKyWrLEDMS%2BeuzZilu91cLhdGbE4eK1oG0IETBO%2F6A5KDkbIE%2BeX91r3aUk5TERHSJ%2FRY2oqmpZvrg%
