# AITestData.py
from robot.api.deco import keyword, library
from ai_client import json_reply

@library
class AITestData:
    def __init__(self, model=None):
        self.model = model

    @keyword("Generate Test Data")
    def generate_test_data(self, type="user_profile", **constraints):
        """
        Example: ${user}=  Generate Test Data  type=user_profile  country=AT  password_policy=strong
        Returns a Python dict.
        """
        system = (
            "You generate realistic test data for automated tests. "
            "Follow the schema, fill plausible values, keep it deterministic-ish, and DO NOT include secrets."
        )
        schema_by_type = {
            "user_profile": {
                "type": "object",
                "properties": {
                    "first_name": {"type":"string"},
                    "last_name": {"type":"string"},
                    "email": {"type":"string"},
                    "phone": {"type":"string"},
                    "password": {"type":"string"},
                    "country": {"type":"string"}
                },
                "required": ["first_name","last_name","email","password"]
            }
        }
        schema = schema_by_type.get(type, {"type":"object"})
        user_prompt = (
            f"Generate one {type} object as JSON matching this JSON Schema:\n"
            f"{schema}\n"
            f"Constraints (optional): {constraints}\n"
            f"Keep values simple and test-friendly."
        )
        return json_reply(system, user_prompt, model=self.model)
