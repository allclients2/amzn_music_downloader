# com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2

import requests

url = "https://music.amazon.co.jp/FE/api/dmls/"

headers = {
    "Authorization": "Bearer Atna|EwMDIJouu-UGMXCDQQnvUsYyWsCWzVVkmdigEDgZXpM7gczdRlbOfxxF20YqH_VgBmJA1CKnlRjWMfgtyxDQ26Qn5yH999i9ZCP0NTjyOuospDas2V_qQXRuIMTR-DK6eH8320fA36jkFp5v1OpfDwBzHEqHivRvN7NwgLEf-DKYM22Li05Atx5GtDlePiLGR6BMo_Yvp51TiSIcNZFoluCmoh9yp6sKEJGrMN9E1TS2VZjdKPy4PLEukmwi_XOtD1Zqj8wm27FONvpfwrmMolgOsN4GYllb6EZausN4uWO8gao9mGUk-TRPppIKdn8qz3FccDEr1mffuZRx5x7OIgZY1C4JcqeBER9ePYwEqs11Z5CuZ_1wp_o8x6ZVQOkFYOT3PIjBvsyS9v_Iokq6ID6TxOpumEOtJVGyi86mQvA1bKIcOzoqXrwZPHaRAjuR1__0jB8",
    "Cookie": "am-loader-experiment=7SBEQKWXH86XAWZWAN47; cwr_u=b1c40e55-b179-42c3-8249-e7980b027571; session-id=357-9334536-5583055; ubid-acbjp=358-3011604-1694212; lc-acbjp=ja_JP; sso-state-acbjp=Xdsso|ZQFkVdKx9Pr702GW7pXrT-p6ZDwj2i9nUaTyWM0N86pz44DTN2KxI_Ebgyu3MawsEKYOXhVXggCx1UQY1HM0akUNjPdT-5EZim_xVHh7sP8kefYA; x-acbjp=e?lp4R33IUTj@Zavy2xosJK5NWphT7NEX?WmqTehcs9FwxNvtwP7?OTymQxUsm0y; at-acbjp=Atza|gQC4RcqsAQEBAb2o-lV5UV7KALZ02CGjtEcv_zVzBtqZs1Zn2QjJTzqdBYF2nFvROEgV641lphKBER5wcBbUQTJvSfziycszFyoQPbcrTbPbBPG0mVtDPapWiOXPDcOLSdkgbK2UvxC7uRqqTjNf5-QzxmU76fY1dJJCZEBdO_ALyqkQUcMm5m1fcTRO5AbzR1qugyoDNa3pzC1Mx9WmXng4kDX9wLAgwBScKr7qvnkELTWK74jfRdh3zo9dxIa7WNG6ms2UMX0a4iRAmDo7a33GrMygpRL2JQXM8UbPMzXdMs8BzvnEv9PVvRArvPvPTu9jaI6PtCPTBOiSSLkOT3DsQmWybn6Utyq6Wg; sess-at-acbjp=CS8pOBYCCgJGMqFt4xo3NVP5YS4nPMdf2cz3UEQaGJ8=; sst-acbjp=Sst1|PQK4yED35CpDTgpw7UJukAOzCe4g7RH_87dLM0-rVAGHC9peOqwCoEn-qWRD6MYHdgplguaSPfyfHszwYCW3U19bDaedx3xsLFAS5xkst09OCv83AJgxNs_852U_c4KHd_zsF9SGx0zYkTCFdJWOO7i7070BHaiwWrjZGT6wlZbI-yEifod-I_F3RBGZDJLsA_FMtm_nGKT-ohqNljnPyq7hyofc890rLpCbFcnCA9yl-n_62c_o18MAtdNV9brejc2ieGOal_AwREKCpZPJW_v0I83A0jTjnQJszcmJHtXfAtI; session-id-time=2082787201l; am-token=eyJkZXZpY2VJZCI6IjM1NzkzMzQ1MzY1NTgzMDU1IiwiaGFzQXV0aGVudGljYXRlZCI6dHJ1ZSwicHJvZmlsZUlkIjoiYW16bjEuYWN0b3IucGVyc29uLmRpZC5BTk1ETEpSSlNBWFVVWFc1NlpDS1IyVE5MWEFJSkxQSFozSlBTTjQ3TkhaUktIV0NIVkFYSVYzVlNVR1pFQUZXVkhUWFNOVlkiLCJndWVzdFRva2VuIjpudWxsLCJndWVzdFViaWQiOm51bGwsImd1ZXN0Q29udGV4dCI6bnVsbCwiaGFzQ29tcGxldGVkRGF0YVRyYW5zZmVyIjpmYWxzZX0%3D; cwr_s=eyJzZXNzaW9uSWQiOiJkMjNkYmE2NC00NjVjLTRkYmQtYjlkOS1kMTFjYzYyZWMzNDMiLCJyZWNvcmQiOnRydWUsImV2ZW50Q291bnQiOjgyLCJwYWdlIjp7InBhZ2VJZCI6ImZpbmQiLCJpbnRlcmFjdGlvbiI6MCwicmVmZXJyZXIiOiIiLCJyZWZlcnJlckRvbWFpbiI6IiIsInN0YXJ0IjoxNzcyMzkwOTQxMTc2fX0=; session-token=kIZE9aN5W2K9nIesdTIVieAVIbgiCX1%2FoRtwXbB9KPOo7QGgSr8FN9qGbd9py4PYmdA8jPbJtJNPBmdrIOsT8tS0lEkGdPByFW3Y3fyoxLWq8f818Z2M4pKA%2BXkzW5ChQhdXi9mZreLd1TUXy2ncJHgM%2BUtOraKyWrLEDMS%2BeuzZilu91cLhdGbE4eK1oG0IETBO%2F6A5KDkbIE%2BeX91r3aUk5TERHSJ%2FRY2oqmpZvrg%3D",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "X-Amz-Target": "com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2",
    "Csrf-Token": "RNNfPr7ibe1DuM4fdk32NSqWE+8i8K90GHP/6d2FQuI=",
    "Csrf-Rnd": "1835056751",
    "Csrf-Ts": "1772393588",
    "Content-Encoding": "amz-1.0",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://music.amazon.co.jp"
}

payload = {
    "DrmType": "WIDEVINE",
    "licenseChallenge": "CAQ=",
    "customerId": "A1ESWSPQYZVGQH",
    "deviceToken": {
        "deviceTypeId": "A16ZV8BU3SN1N3",
        "deviceId": "35793345365583055"
    },
    "appInfo": {
        "musicAgent": "Maestro/1.0 WebCP/1.0.9527.0 (452f-e941-WebC-5485-5446b)"
    },
    "Authorization": "Bearer Atna|EwMDIJouu-UGMXCDQQnvUsYyWsCWzVVkmdigEDgZXpM7gczdRlbOfxxF20YqH_VgBmJA1CKnlRjWMfgtyxDQ26Qn5yH999i9ZCP0NTjyOuospDas2V_qQXRuIMTR-DK6eH8320fA36jkFp5v1OpfDwBzHEqHivRvN7NwgLEf-DKYM22Li05Atx5GtDlePiLGR6BMo_Yvp51TiSIcNZFoluCmoh9yp6sKEJGrMN9E1TS2VZjdKPy4PLEukmwi_XOtD1Zqj8wm27FONvpfwrmMolgOsN4GYllb6EZausN4uWO8gao9mGUk-TRPppIKdn8qz3FccDEr1mffuZRx5x7OIgZY1C4JcqeBER9ePYwEqs11Z5CuZ_1wp_o8x6ZVQOkFYOT3PIjBvsyS9v_Iokq6ID6TxOpumEOtJVGyi86mQvA1bKIcOzoqXrwZPHaRAjuR1__0jB8"
}

response = requests.post(
    url,
    headers=headers,
    json=payload
)


print("Status Code:", response.status_code)
print("Response Body:", response.text)