from robot.api.deco import keyword, library
from ai_client import json_reply


@library
class AITestData:
    """
    A Robot Framework library to generate AI‑assisted test data.  The library
    delegates to ``ai_client.json_reply`` to ask a language model for data that
    matches a given JSON schema and optional constraints.  By default the
    ``model`` argument is taken from the environment variable ``OLLAMA_MODEL``
    (via ``ai_client``), but you can override it by passing a model name to
    the class constructor.
    """

    def __init__(self, model: str | None = None) -> None:
        # Store the model name so that it can be passed to the AI client on calls.
        self.model = model

    @keyword("Generate Test Data")
    def generate_test_data(self, type: str = "user_profile", **constraints):
        """
        Generate a single data record matching a given JSON schema.

        :param type: The type of test data to generate.  Currently the only
            supported built‑in type is ``user_profile``.
        :param constraints: Optional keyword constraints to apply (e.g. ``country``,
            ``password_policy``).  These will be included in the prompt sent to
            the model.

        The method returns a Python dictionary representing the generated data.

        **Example usage in Robot Framework:**

        ```${user}=    Generate Test Data    type=user_profile    country=AT    password_policy=strong```.

        This will return a dictionary with keys like ``first_name``, ``last_name``,
        ``email`` and ``password``.  The values will be deterministic and
        test‑friendly, and no secrets or sensitive information will be included.
        """
        # System message that instructs the model to act as a test data generator.
        system = (
            "You generate realistic test data for automated tests. "
            "Follow the schema, fill plausible values, keep it deterministic-ish, and DO NOT include secrets."
        )
        # Define simple JSON schemas for supported types.  You can extend this
        # dictionary with your own schemas if you need to generate other data types.
        schema_by_type = {
            "user_profile": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "password": {"type": "string"},
                    "country": {"type": "string"},
                },
                "required": ["first_name", "last_name", "email", "password"],
            }
        }
        # Fallback to a generic object schema if the requested type is unknown.
        schema = schema_by_type.get(type, {"type": "object"})

        # Compose the user prompt for the AI.  Constraints are interpolated into
        # the prompt to guide the model.  Keep values simple to avoid complex
        # nested structures that may be harder to consume in tests.
        user_prompt = (
            f"Generate one {type} object as JSON matching this JSON Schema:\n"
            f"{schema}\n"
            f"Constraints (optional): {constraints}\n"
            f"Keep values simple and test-friendly."
        )
        # Delegate to the AI client.  Use the stored model; if ``self.model`` is
        # None, ``json_reply`` will fall back to the OLLAMA_MODEL environment
        # variable via the underlying client implementation.
        return json_reply(system, user_prompt, model=self.model)