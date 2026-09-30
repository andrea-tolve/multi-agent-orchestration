import argparse
import json
import time
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Configuration
CONTAINER_PORT = 8000

# In‑memory game state
game_state = {
    "player_move": None,
    "opponent_move": None,
    "result": None,
    "completed": "no",
    "last_update": time.time()
}

# Helper to compute RPS result
def compute_result():
    p = game_state["player_move"]
    o = game_state["opponent_move"]
    if not p or not o:
        return
    if p == o:
        res = "tie"
    elif (p == "rock" and o == "scissors") or (p == "scissors" and o == "paper") or (p == "paper" and o == "rock"):
        res = "player wins"
    else:
        res = "opponent wins"
    game_state["result"] = res
    game_state["completed"] = "yes"
    game_state["last_update"] = time.time()

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "local-rps-player",
        "description": "Serve a web page with three buttons (rock, paper, scissors). Accept a POST with the player's move, store it, accept a POST from the partner containing the opponent's move, and when both moves are present compute and return the result."
    },
    "paths": {
        "/api/rps/play": {
            "post": {
                "summary": "Submit player's move",
                "description": "Accepts the player's move (rock, paper, or scissors) and stores it. If the opponent's move is already present, the result is computed.",
                "requestBody": {
                    "required": "yes",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "Player's move: rock, paper, or scissors"}
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Move accepted",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string"},
                                        "status": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/opponent-move": {
            "post": {
                "summary": "Submit opponent's move",
                "description": "Receives the opponent's move (rock, paper, or scissors) and stores it. If the player's move is already present, the result is computed.",
                "requestBody": {
                    "required": "yes",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "Opponent's move: rock, paper, or scissors"}
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Opponent move accepted",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string"},
                                        "status": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/status": {
            "get": {
                "summary": "Get current game status",
                "description": "Returns the result when both moves are present and a completed flag. No blocking; client should poll.",
                "responses": {
                    "200": {
                        "description": "Current status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"type": "string", "description": "Result of the game (player wins, opponent wins, tie) or empty string if not ready"},
                                        "completed": {"type": "string", "description": "'yes' when both moves are present, otherwise 'no'"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api-schema": {
            "get": {
                "summary": "OpenAPI schema",
                "description": "Returns the OpenAPI 3.0.0 schema for this service.",
                "responses": {
                    "200": {
                        "description": "Schema JSON",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/": {
            "get": {
                "summary": "UI page",
                "description": "Serves the HTML page with rock‑paper‑scissors buttons.",
                "responses": {
                    "200": {
                        "description": "HTML page",
                        "content": {
                            "text/html": {
                                "schema": {
                                    "type": "string"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@app.route('/api-schema')
def api_schema():
    return jsonify(API_SCHEMA)

@app.route('/')
def ui():
    return send_file('ui.html')

@app.route('/api/rps/play', methods=['POST'])
def play():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if not move:
        return jsonify({"message": "Missing move", "status": "error"}), 400
    game_state["player_move"] = move.lower()
    game_state["last_update"] = time.time()
    # recompute if opponent already moved
    if game_state["opponent_move"]:
        compute_result()
    else:
        game_state["result"] = None
        game_state["completed"] = "no"
    return jsonify({"message": "Player move recorded", "status": "ok"})

@app.route('/api/opponent-move', methods=['POST'])
def opponent_move():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if not move:
        return jsonify({"message": "Missing move", "status": "error"}), 400
    game_state["opponent_move"] = move.lower()
    game_state["last_update"] = time.time()
    # recompute if player already moved
    if game_state["player_move"]:
        compute_result()
    else:
        game_state["result"] = None
        game_state["completed"] = "no"
    return jsonify({"message": "Opponent move recorded", "status": "ok"})

@app.route('/api/status', methods=['GET'])
def status():
    result = game_state.get("result") or ""
    completed = game_state.get("completed", "no")
    return jsonify({"data": result, "completed": completed})

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
