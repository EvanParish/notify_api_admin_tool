# the GET /service endpoint returns a list of services in this format
from sqlalchemy import null


service_data = {
    "data": [
        {
            "active": True,
            "all_template_folders": [],
            "allow_fallback": False,
            "annual_billing": [],
            "consent_to_research": None,
            "contact_link": None,
            "count_as_live": True,
            "created_by": "a8d82fd1-a306-4073-8506-dc02f9a2855c",
            "crown": True,
            "email_branding": None,
            "email_from": None,
            "email_provider_id": "bb20d8fa-0413-45ef-9244-53ea929446ce",
            "go_live_at": None,
            "go_live_user": None,
            "id": "d6aa2c68-a2d9-4437-ab19-3ae8eb202553",
            "inbound_numbers": [],
            "letter_contact_block": None,
            "message_limit": 100000,
            "name": "VA Notify",
            "organisation": None,
            "organisation_type": None,
            "p2p_enabled": True,
            "permissions": [
                "international_sms",
                "push",
                "email",
                "sms"
            ],
            "prefix_sms": False,
            "rate_limit": 3000,
            "research_mode": False,
            "restricted": False,
            "sending_domain": None,
            "service_callback": [
                "64250b4b-4efe-4d16-bd7c-3ef11b9be998"
            ],
            "service_data_retention": [],
            "sms_provider_id": "1e566b9f-3c98-4857-9322-8968a37ad9ef",
            "smtp_user": None,
            "users": [
                "61cd22b4-1a4f-4a7b-8007-13669d61d6e4",
                "a17cc11f-739c-43d9-aa22-e03bb0f9ae6a"
            ],
            "version": 16,
            "volume_email": None,
            "volume_letter": None,
            "volume_sms": None,
            "whitelist": []
        }
    ]
}

# The GET /service/{service_id}/template endpoint returns an object with a list of template data
template_data={
    "data": [
        {
            "archived": False,
            "communication_item_id": None,
            "content": "test otp message ((OTP))",
            "content_as_html": None,
            "content_as_plain_text": None,
            "created_at": "2023-05-02T20:59:37.013017",
            "created_by": "40e5f846-02e3-4a23-b890-bee41d8e2a1f",
            "folder": None,
            "hidden": False,
            "id": "e98f2fd4-f307-4092-be15-34a8d903aaaa",
            "name": "0501SMS Test edit.",
            "postage": None,
            "process_type": "normal",
            "provider_id": None,
            "redact_personalisation": False,
            "reply_to": None,
            "reply_to_email": None,
            "service": "d6aa2c68-a2d9-4437-ab19-3ae8eb202553",
            "service_letter_contact": None,
            "subject": None,
            "template_redacted": "e98f2fd4-f307-4092-be15-34a8d903deda",
            "template_type": "sms",
            "updated_at": "2024-04-23T15:27:47.100809",
            "version": 3
        },
        {
            "archived": False,
            "communication_item_id": None,
            "content": "Testing...((name))",
            "content_as_html": None,
            "content_as_plain_text": None,
            "created_at": "2023-01-09T20:07:31.572205",
            "created_by": "d840ff13-57b6-4f97-9137-965484f22299",
            "folder": None,
            "hidden": False,
            "id": "aef3658a-1c78-443a-9c74-688ee96f18be",
            "name": "10.13 test",
            "postage": None,
            "process_type": "normal",
            "provider_id": None,
            "redact_personalisation": False,
            "reply_to": None,
            "reply_to_email": "jacob.uhteg@va.gov",
            "service": "d6aa2c68-a2d9-4437-ab19-3ae8eb202553",
            "service_letter_contact": None,
            "subject": "Test ((title))",
            "template_redacted": "aef3658a-1c78-443a-9c74-688ee96f18be",
            "template_type": "email",
            "updated_at": "2025-06-06T20:48:22.963264",
            "version": 9
        }
    ]
}

# The GET /service/{service_id}/api-key endpoint returns a list of API keys like this
api_key_data = {
    "apiKeys": [
        {
            "created_at": "2025-05-23T19:22:04.550577",
            "created_by": "859d6821-e9bd-409a-a595-1be7a8064b21",
            "expiry_date": "2025-11-19T19:22:04.549876",
            "id": "79bc99c0-241e-48dd-b49b-6752e5899dce",
            "key_type": "normal",
            "name": "my-apikey",
            "revoked": False,
            "updated_at": None,
            "version": 1
        },
        {
            "created_at": "2025-12-18T19:36:17.035499",
            "created_by": "0b7de540-7202-4ace-8a2a-1a11674f73b5",
            "expiry_date": "2025-12-24T11:59:00",
            "id": "42ebf706-4d1c-421f-bf92-ab938b58f5fd",
            "key_type": "normal",
            "name": "postmantestapikey-expiry-in-2-days",
            "revoked": False,
            "updated_at": None,
            "version": 1
        }
    ]
}