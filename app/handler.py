import os
import json
import uuid
import logging
import ssl
import urllib.request
from urllib.error import HTTPError, URLError

import boto3
from botocore.exceptions import ClientError

# AWS clients
SSM = boto3.client("ssm")
DDB = boto3.client("dynamodb")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"  # small & cheap model; change if needed


def get_secret(param_name: str) -> str:
    """
    Get OpenAI API key from SSM Parameter Store.
    The Lambda environment variable OPENAI_PARAM holds the parameter name.
    """
    resp = SSM.get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def build_fallback_plan(calories: int, protein_g: int) -> dict:
    """
    Simple static fallback plan used when OpenAI fails or rate limits.
    Adjusts totals a bit based on requested calories/protein.
    """
    base_totals = {"kcal": 2000, "protein": 180, "carbs": 220, "fat": 50}

    # Rough scaling so it looks responsive
    scale = calories / 2000 if calories else 1.0
    protein_scale = protein_g / 180 if protein_g else 1.0

    totals = {
        "kcal": int(base_totals["kcal"] * scale),
        "protein": int(base_totals["protein"] * protein_scale),
        "carbs": int(base_totals["carbs"] * scale),
        "fat": int(base_totals["fat"] * scale),
    }

    plan = {
        "meals": [
            {
                "name": "Fallback Breakfast",
                "ingredients": [
                    "rolled oats (80g)",
                    "whey protein (1 scoop)",
                    "banana (1 medium)",
                    "water or low-fat milk",
                ],
                "macros": {
                    "kcal": 500,
                    "protein": 40,
                    "carbs": 65,
                    "fat": 8,
                },
                "prep": (
                    "Microwave oats with water or milk, then stir in "
                    "whey protein and sliced banana."
                ),
            },
            {
                "name": "Fallback Lunch",
                "ingredients": [
                    "chicken breast (150g)",
                    "rice (100g dry)",
                    "mixed veggies (frozen, 100g)",
                ],
                "macros": {
                    "kcal": 650,
                    "protein": 55,
                    "carbs": 75,
                    "fat": 12,
                },
                "prep": (
                    "Grill or pan-cook chicken, cook rice, heat veggies. "
                    "Serve together and season to taste."
                ),
            },
            {
                "name": "Fallback Dinner",
                "ingredients": [
                    "93% lean ground turkey (150g)",
                    "whole wheat pasta (90g dry)",
                    "tomato sauce (100g)",
                ],
                "macros": {
                    "kcal": 700,
                    "protein": 55,
                    "carbs": 80,
                    "fat": 16,
                },
                "prep": (
                    "Brown turkey in a pan, boil pasta, add tomato sauce. "
                    "Combine and season with salt, pepper, and herbs."
                ),
            },
        ],
        "totals": totals,
        "shopping_list": [
            "rolled oats",
            "whey protein",
            "bananas",
            "chicken breast",
            "rice",
            "mixed frozen veggies",
            "93% lean ground turkey",
            "whole wheat pasta",
            "tomato sauce",
        ],
        "notes": (
            "This is a static fallback plan used when the OpenAI API call "
            "fails or is rate limited (HTTP 429). Once your OpenAI quota is "
            "available again, the API will start returning fully "
            "AI-generated plans instead."
        ),
    }
    return plan


def call_openai_or_fallback(api_key: str, prompt: str, calories: int, protein_g: int) -> str:
    """
    Try to call OpenAI. If anything goes wrong (HTTP error, network error, etc),
    log it and return a JSON string for a fallback plan instead.
    This function NEVER raises, so Lambda won't crash because of OpenAI.
    """
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a nutrition planner. "
                        "You ALWAYS respond with valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return content
    except HTTPError as e:
        logger.warning("HTTPError from OpenAI: %s", e)
    except URLError as e:
        logger.warning("URLError from OpenAI: %s", e)
    except Exception as e:
        logger.warning("Unexpected error when calling OpenAI: %s", e)

    # Any failure ends up here
    fallback = build_fallback_plan(calories, protein_g)
    return json.dumps(fallback)


def lambda_handler(event, context):
    """
    AWS Lambda entrypoint.
    Expects an API Gateway HTTP event with a JSON body containing:
      - calories (int)
      - protein_g (int)
      - dislikes (list of strings)
      - budget_per_day_usd (float/int)
    This handler NEVER throws – it always returns a JSON body with statusCode 200.
    """
    logger.info("Event: %s", json.dumps(event))

    try:
        body = json.loads(event.get("body") or "{}")

        calories = int(body.get("calories", 2000))
        protein_g = int(body.get("protein_g", 180))
        dislikes = body.get("dislikes", ["pickles"])
        budget = body.get("budget_per_day_usd", 8)

        prompt = (
            f"Build a 1-day meal plan at about {calories} kcal and "
            f"{protein_g} g protein. Avoid these foods: {dislikes}. "
            f"Budget: ${budget} per day. "
            "Return JSON with keys: "
            "meals (array of {name, ingredients, macros:{kcal,protein,carbs,fat}, prep}), "
            "totals ({kcal,protein,carbs,fat}), "
            "shopping_list (array of strings), "
            "notes (string)."
        )

        # Get API key and call OpenAI (or fallback)
        api_key = get_secret(os.environ["OPENAI_PARAM"])
        plan_json_str = call_openai_or_fallback(api_key, prompt, calories, protein_g)

        # Validate that the string is valid JSON
        plan = json.loads(plan_json_str)

        plan_id = str(uuid.uuid4())
        DDB.put_item(
            TableName=os.environ["TABLE_NAME"],
            Item={
                "plan_id": {"S": plan_id},
                "request": {"S": json.dumps(body)},
                "plan": {"S": json.dumps(plan)},
            },
        )

        resp_body = {
            "plan_id": plan_id,
            "plan": plan,
            "source": "openai_or_fallback",
        }

    except ClientError as e:
        logger.exception("AWS ClientError")
        resp_body = {
            "error": "AWS error",
            "detail": str(e),
        }
    except Exception as e:
        logger.exception("Unhandled exception in Lambda")
        resp_body = {
            "error": "internal_error",
            "detail": str(e),
        }

    # IMPORTANT: Always return 200 so API Gateway doesn't hide our error JSON
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(resp_body),
    }