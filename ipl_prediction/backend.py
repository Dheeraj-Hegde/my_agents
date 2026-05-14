import os
import json
import asyncio
import logging
import subprocess
import sys
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("IPL-Backend")

# Get API Key from environment
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    logger.warning("GOOGLE_API_KEY not found in environment. Please set it.")

# Initialize Google GenAI Client
client = genai.Client(api_key=api_key)

# Global variables for MCP session
mcp_session: Optional[ClientSession] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_session
    
    # Define server parameters
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "mcp_server.py"],
        env=os.environ.copy()
    )
    
    # Use anyio directly to manage the context
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_session = session
            logger.info("Connected to MCP Server successfully.")
            yield
            logger.info("Shutting down MCP Session...")

app = FastAPI(title="IPL Prediction Backend", lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class PredictionRequest(BaseModel):
    team_a: str
    team_b: str
    venue: str

class PredictionResponse(BaseModel):
    reasoning_steps: List[Dict[str, str]]
    final_prediction: Dict[str, Any]

async def call_mcp_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Calls an MCP tool and returns the result as a string."""
    if not mcp_session:
        return "Error: MCP session not initialized."
    
    logger.info(f"Calling MCP Tool: {name} with args: {arguments}")
    result = await mcp_session.call_tool(name, arguments)
    
    # result.content is a list of content blocks
    return "\n".join([block.text for block in result.content if hasattr(block, 'text')])

def clean_schema(schema: Any) -> Any:
    """Recursively removes fields not supported by Gemini's Schema object and ensures valid structure."""
    if not isinstance(schema, dict):
        return schema
        
    # Gemini is very picky about these fields
    allowed_keys = {"type", "properties", "required", "items", "description", "enum", "format"}
    res = {k: clean_schema(v) for k, v in schema.items() if k in allowed_keys}
    
    # Ensure type is present
    if "type" not in res:
        if "properties" in res:
            res["type"] = "OBJECT"
        elif "items" in res:
            res["type"] = "ARRAY"
    
    # Map types to uppercase as some versions prefer it
    if "type" in res and isinstance(res["type"], str):
        res["type"] = res["type"].upper()
        
    # CRITICAL: Only include required fields that actually exist in properties
    if "required" in res and "properties" in res:
        res["required"] = [field for field in res["required"] if field in res["properties"]]
        if not res["required"]:
            del res["required"]
            
    return res

async def get_prediction_from_gemini(team_a, team_b, venue):
    """Handles the chat loop with Gemini and MCP tools using the new google-genai SDK."""
    # 1. Discover tools from MCP
    tools_response = await mcp_session.list_tools()
    
    # 2. Setup Tool definitions (Single tool with multiple function declarations)
    function_declarations = []
    for tool in tools_response.tools:
        function_declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=clean_schema(tool.inputSchema)
            )
        )
    
    genai_tools = [types.Tool(function_declarations=function_declarations)]
    
    model_name = 'gemini-3.1-flash-lite'
    
    prompt = f"""
    You are an elite IPL Cricket Analyst. Predict the outcome of this match:
    Team A: {team_a}
    Team B: {team_b}
    Venue: {venue}
    
    Follow this strict Reasoning Protocol:
    1. [LOOKUP] Get league standings and recent form.
    2. [LOOKUP] Get injury reports for both teams.
    3. [LOGIC] Analyze venue environment and weather.
    4. [LOOKUP] Check Head-to-Head stats and key player matchups.
    5. [ARITHMETIC] Calculate score projections based on data.
    6. [SYNTHESIS] Provide a final win probability and verdict.
    
    Final output MUST be a JSON object with this structure:
    {{
        "reasoning_steps": [
            {{"tag": "[LOOKUP]", "title": "Step 1: Data Gathering", "content": "..."}},
            ...
        ],
        "final_prediction": {{
            "team_a": "{team_a}",
            "team_b": "{team_b}",
            "prob_a": 58,
            "prob_b": 42,
            "venue_verdict": "...",
            "matchups": [
                {{"batter": "Virat Kohli", "bowler": "Jasprit Bumrah", "edge": "Team A"}},
                ...
            ]
        }}
    }}
    """
    
    # Chat with tool calling loop
    # In google-genai, we can use client.models.generate_content with config=types.GenerateContentConfig(tools=genai_tools)
    
    messages = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    
    for _ in range(12): # Increased iterations for complex reasoning
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=messages,
                config=types.GenerateContentConfig(
                    tools=genai_tools,
                    temperature=0.7,
                )
            )
        except Exception as e:
            logger.error(f"Gemini call failed: {e}")
            raise
        
        # Add model's response to history
        messages.append(response.candidates[0].content)
        
        # Check for function calls
        function_calls = [part.function_call for part in response.candidates[0].content.parts if part.function_call]
        
        if not function_calls:
            # If no function calls, the last message should contain the text
            text_parts = [part.text for part in response.candidates[0].content.parts if part.text]
            if text_parts:
                return "".join(text_parts)
            break
            
        tool_responses = []
        for fc in function_calls:
            tool_name = fc.name
            args = fc.args
            
            logger.info(f"Calling MCP Tool: {tool_name} with {args}")
            tool_result = await call_mcp_tool(tool_name, args)
            
            tool_responses.append(types.Part(
                function_response=types.FunctionResponse(
                    name=tool_name,
                    response={'result': tool_result}
                )
            ))
        
        # Add tool results to history
        messages.append(types.Content(role="user", parts=tool_responses))
    
    raise ValueError("Gemini failed to return a final text response.")

@app.post("/predict", response_model=PredictionResponse)
async def predict(request: PredictionRequest):
    if not mcp_session:
        raise HTTPException(status_code=500, detail="MCP Server not connected.")
    
    try:
        text_content = await get_prediction_from_gemini(request.team_a, request.team_b, request.venue)
        
        # Clean up possible markdown code blocks
        if "```json" in text_content:
            text_content = text_content.split("```json")[1].split("```")[0].strip()
        elif "```" in text_content:
            text_content = text_content.split("```")[1].split("```")[0].strip()
        else:
            start = text_content.find('{')
            end = text_content.rfind('}')
            if start != -1 and end != -1:
                text_content = text_content[start:end+1]
            
        data = json.loads(text_content)
        return PredictionResponse(**data)
        
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/venues")
async def get_venues():
    if not mcp_session:
        raise HTTPException(status_code=500, detail="MCP Server not connected.")
    try:
        result = await call_mcp_tool("get_available_venues", {})
        return json.loads(result)
    except Exception as e:
        logger.error(f"Failed to fetch venues: {e}")
        return ["Wankhede Stadium", "M. Chinnaswamy Stadium", "Eden Gardens"]

@app.get("/match-venue")
async def get_match_venue(team_a: str, team_b: str):
    if not mcp_session:
        raise HTTPException(status_code=500, detail="MCP Server not connected.")
    try:
        result = await call_mcp_tool("get_match_venue", {"team_a": team_a, "team_b": team_b})
        return result
    except Exception as e:
        logger.error(f"Failed to fetch match venue: {e}")
        return f"{team_a} Home Ground"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
