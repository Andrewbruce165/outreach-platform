# AI Function Calling & Webhooks - Implementation Guide

## Overview

The Telegram API supports OpenAI Function Calling, allowing the AI to extract structured data from conversations and send it to external webhooks for processing.

## Architecture

```
Incoming Message → AI Engine → OpenAI GPT-4 (with tools) → Function Call Detection
                                                              ↓
                                                    Parse Arguments (JSON)
                                                              ↓
                                                    Execute Webhook (HTTP POST)
                                                              ↓
                                                    External System Receives Data
```

## How It Works

### 1. Configure webhook_functions in AI Context

The `webhook_functions` field in `ai_contexts` table stores an array of function definitions in JSONB format.

### 2. Expected Format

```json
[
  {
    "name": "record_price_quote",
    "description": "Record a price quote from the supplier",
    "webhook_url": "https://your-app.com/webhooks/record-price",
    "parameters": [
      {
        "name": "product_name",
        "type": "string",
        "description": "Name of the product being quoted",
        "required": true
      },
      {
        "name": "price_per_unit",
        "type": "number",
        "description": "Price per unit in rubles",
        "required": true
      },
      {
        "name": "unit",
        "type": "string",
        "description": "Unit of measurement (e.g., kg, ton, piece)",
        "required": false
      },
      {
        "name": "delivery_date",
        "type": "string",
        "description": "Expected delivery date in ISO format or natural language",
        "required": false
      }
    ]
  },
  {
    "name": "schedule_callback",
    "description": "Schedule a callback when the supplier asks to be called back",
    "webhook_url": "https://your-app.com/webhooks/schedule-callback",
    "parameters": [
      {
        "name": "preferred_time",
        "type": "string",
        "description": "When they want to be called (e.g., 'tomorrow at 2pm', 'in 30 minutes')",
        "required": true
      },
      {
        "name": "reason",
        "type": "string",
        "description": "Reason for the callback",
        "required": false
      }
    ]
  }
]
```

### 3. Function Definition Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique function identifier (snake_case recommended) |
| `description` | string | Yes | Clear description telling AI when to use this function |
| `webhook_url` | string | Yes | HTTPS endpoint that will receive the data |
| `parameters` | array | Yes | List of parameter definitions (can be empty array) |

### 4. Parameter Definition Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Parameter name (snake_case recommended) |
| `type` | string | Yes | JSON Schema type: `string`, `number`, `integer`, `boolean`, `object`, `array` |
| `description` | string | Yes | Clear description for AI to understand what to extract |
| `required` | boolean | No | Whether this parameter is required (default: false) |

### 5. Webhook Payload Format

When AI calls a function, your webhook receives:

```json
{
  "function": "record_price_quote",
  "data": {
    "product_name": "Морковь",
    "price_per_unit": 45.50,
    "unit": "kg",
    "delivery_date": "2026-01-25"
  },
  "context": {
    "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
    "contact_phone": "+79991234567",
    "contact_name": "Иван Петров",
    "contact_telegram_id": 123456789,
    "sender_id": "789e4567-e89b-12d3-a456-426614174999",
    "sender_slug": "ags-foods",
    "sender_name": "AGS Foods Bot",
    "ai_context_id": "456e7890-e89b-12d3-a456-426614174111"
  },
  "timestamp": "2026-01-20T12:34:56.789Z"
}
```

### 6. Implementation Flow

#### Step 1: AI Engine builds OpenAI tools
```python
# ai_engine.py: build_tools() method
tools = [
    {
        "type": "function",
        "function": {
            "name": "record_price_quote",
            "description": "Record a price quote from the supplier",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Name of the product being quoted"
                    },
                    "price_per_unit": {
                        "type": "number",
                        "description": "Price per unit in rubles"
                    }
                },
                "required": ["product_name", "price_per_unit"]
            }
        }
    }
]
```

#### Step 2: OpenAI GPT-4 processes conversation
- AI reads conversation history and new message
- If supplier mentions price info, AI decides to call `record_price_quote`
- OpenAI returns tool_calls in response

#### Step 3: AI Engine executes webhook
```python
# ai_engine.py: execute_webhook() method
async with httpx.AsyncClient(timeout=10.0) as http_client:
    response = await http_client.post(
        webhook_url,
        json=payload,
        headers={"Content-Type": "application/json"}
    )
```

#### Step 4: AI generates text response
- If AI only called function without text, second API call is made
- AI generates friendly text response to send to supplier
- Example: "Спасибо! Я записал цену на морковь - 45.50 руб/кг."

## Best Practices

### Function Naming
- Use descriptive snake_case names: `record_price_quote`, `schedule_callback`
- Avoid generic names: `save_data`, `process_info`

### Descriptions
- Be specific about when AI should use the function
- Include examples in description if helpful
- Tell AI what data to extract

Good:
```json
{
  "description": "Record a price quote when supplier mentions specific product prices. Extract product name, price per unit, and unit of measurement."
}
```

Bad:
```json
{
  "description": "Save data"
}
```

### Parameters
- Use clear, descriptive names
- Add detailed descriptions
- Mark only truly essential parameters as required
- Use appropriate JSON types (number for prices, not string)

### Webhook URLs
- Always use HTTPS in production
- Implement proper authentication (API keys, tokens)
- Return 200-299 status code for success
- Return error details in response body if failed
- Handle timeouts gracefully (webhook has 10s timeout)

### Error Handling
The system handles these errors automatically:
- JSON parsing errors in function arguments
- Webhook timeout (10 seconds)
- HTTP errors (connection, status codes)
- OpenAI API errors

Check logs for webhook failures:
```
✅ Webhook отправлен: record_price_quote → https://...
⚠️ Webhook вернул 500: Internal Server Error
❌ Timeout при вызове webhook record_price_quote
```

## Testing Function Calling

### 1. Create a Test Webhook

Use webhook.site or create a simple endpoint:

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/webhooks/test")
async def test_webhook(request: Request):
    data = await request.json()
    print(f"Received: {data}")
    return {"status": "success"}
```

### 2. Add Function to AI Context

```sql
UPDATE ai_contexts
SET webhook_functions = '[
  {
    "name": "test_function",
    "description": "Test function - call this when user mentions a test",
    "webhook_url": "https://your-test-url.com/webhooks/test",
    "parameters": [
      {
        "name": "test_data",
        "type": "string",
        "description": "The test data mentioned",
        "required": true
      }
    ]
  }
]'::jsonb
WHERE id = 'your-context-id';
```

### 3. Send Test Message

Send a message that triggers the function:
```
"Это тест с данными XYZ123"
```

### 4. Verify in Logs

Check listener logs:
```
🤖 Генерируем ответ для Тестер... (tools: 1)
🔧 AI вызвал 1 функций
📤 Функция: test_function, аргументы: {'test_data': 'XYZ123'}
✅ Webhook отправлен: test_function → https://your-test-url.com/webhooks/test
```

## Security Considerations

### Webhook URL Validation
Currently, there's NO validation on webhook URLs. This is a security risk (SSRF vulnerability).

**Recommendation:** Add URL validation in `execute_webhook`:
```python
from urllib.parse import urlparse

def is_safe_webhook_url(url: str) -> bool:
    parsed = urlparse(url)

    # Only allow HTTPS
    if parsed.scheme != "https":
        return False

    # Block internal/private IPs
    hostname = parsed.hostname
    if hostname in ["localhost", "127.0.0.1", "0.0.0.0"]:
        return False

    # Add your whitelist
    allowed_domains = ["your-app.com", "your-webhook-provider.com"]
    if not any(hostname.endswith(domain) for domain in allowed_domains):
        return False

    return True
```

### Webhook Authentication
Your webhook endpoint should verify requests are from your system:

```python
@app.post("/webhooks/record-price")
async def record_price(request: Request):
    # Verify API key
    api_key = request.headers.get("X-API-Key")
    if api_key != os.environ["WEBHOOK_API_KEY"]:
        raise HTTPException(401, "Unauthorized")

    data = await request.json()
    # Process data...
```

Update execute_webhook to include auth:
```python
headers = {
    "Content-Type": "application/json",
    "X-API-Key": os.environ.get("WEBHOOK_API_KEY")
}
```

## Troubleshooting

### AI Not Calling Functions
1. Check function descriptions are clear
2. Verify webhook_functions format is correct
3. Test with explicit trigger phrases
4. Check AI context is loaded properly

### Webhook Not Receiving Data
1. Verify webhook_url is accessible from server
2. Check webhook_url uses HTTPS
3. Test webhook manually with curl
4. Check firewall/network settings

### JSON Parsing Errors
1. Check parameter types match data
2. Verify webhook_functions JSON is valid
3. Look for special characters in function names

### Webhook Timeouts
1. Ensure webhook responds within 10 seconds
2. Make webhook processing async if possible
3. Return 200 immediately, process in background

## Example: Real-World Use Case

### Scenario: Agricultural Supplier Communication

**Goal:** Extract product pricing and availability from supplier conversations.

**Webhook Functions:**
```json
[
  {
    "name": "record_product_availability",
    "description": "Record when supplier confirms product availability, quantity, and delivery timeframe",
    "webhook_url": "https://app.agsfoods.com/api/webhooks/availability",
    "parameters": [
      {
        "name": "products",
        "type": "array",
        "description": "List of available products",
        "required": true
      },
      {
        "name": "quantities",
        "type": "array",
        "description": "Available quantities for each product",
        "required": true
      },
      {
        "name": "delivery_timeframe",
        "type": "string",
        "description": "When products can be delivered",
        "required": false
      }
    ]
  }
]
```

**Example Conversation:**
```
Supplier: У нас есть морковь - 500 кг и картофель - 1 тонна.
          Можем доставить завтра.

AI: [Calls record_product_availability]
    → products: ["морковь", "картофель"]
    → quantities: ["500 kg", "1 ton"]
    → delivery_timeframe: "завтра"

AI Response: "Отлично! Я записал наличие:
             - Морковь: 500 кг
             - Картофель: 1 тонна
             Доставка: завтра

             Коллеги свяжутся с вами для подтверждения заказа."
```

**Your Webhook Processes Data:**
```python
@app.post("/api/webhooks/availability")
async def handle_availability(request: Request):
    data = await request.json()

    # Extract info
    products = data["data"]["products"]
    quantities = data["data"]["quantities"]
    contact = data["context"]["contact_name"]

    # Create records in your system
    for product, qty in zip(products, quantities):
        await db.availability_records.create({
            "supplier_id": data["context"]["contact_telegram_id"],
            "supplier_name": contact,
            "product": product,
            "quantity": qty,
            "reported_at": data["timestamp"]
        })

    # Notify sales team
    await notify_sales_team(contact, products)

    return {"status": "recorded"}
```

## Summary

The function calling implementation allows:
1. ✅ AI to extract structured data from conversations
2. ✅ Automatic webhook execution with parsed data
3. ✅ Full conversation context passed to webhooks
4. ✅ Error handling for JSON parsing, timeouts, HTTP errors
5. ✅ Flexible parameter definitions (required/optional, various types)
6. ✅ Seamless integration with OpenAI GPT-4 tools

**Current Issues:**
- ⚠️ No webhook URL validation (SSRF risk)
- ⚠️ No webhook authentication in execute_webhook
- ⚠️ No retry mechanism for failed webhooks

**Implementation is functional and working as designed.** The documented format and flow match the actual code implementation in `ai_engine.py`.
