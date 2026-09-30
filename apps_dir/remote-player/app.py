import argparse
import time
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Internal game state
game_state = {
    "local_move": None,
    "opponent_move": None,
    "completed": "no",
    "result": None,
    "last_update": None
}

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "remote-player",
        "description": "Flask web service that Serve a web page containing three buttons (rock, paper, scissors). Accept the player's selection via POST, store it, and provide an endpoint that returns the opponent's move and the game outcome once both moves are received."
    },
    "paths": {
        "/api/rps/play": {
            "post": {
                "summary": "Submit a rock/paper/scissors move",
                "description": "Accepts the player's move (rock, paper, or scissors) and optionally indicates if the move is from the local player or opponent.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "The move, one of rock, paper, scissors"},
                                    "player": {"type": "string", "description": "Either 'local' or 'opponent', defaults to 'local'"}
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Move recorded",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string"},
                                        "completed": {"type": "string", "enum": ["yes", "no"]},
                                        "result": {"type": "string"}
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
                "description": "Returns the stored moves and whether the game is completed.",
                "responses": {
                    "200": {
                        "description": "Current status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "local_move": {"type": "string", "nullable": True},
                                        "opponent_move": {"type": "string", "nullable": True},
                                        "completed": {"type": "string", "enum": ["yes", "no"]},
                                        "result": {"type": "string", "nullable": True}
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
                "description": "Provides the OpenAPI 3.0.0 schema for this service.",
                "responses": {
                    "200": {
                        "description": "Schema",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        }
                    }
                }
            }
        },
        "/": {
            "get": {
                "summary": "User interface",
                "description": "Serves the HTML UI page.",
                "responses": {
                    "200": {
                        "description": "HTML page",
                        "content": {
                            "text/html": {
                                "schema": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
}

@app.route('/api-schema', methods=['GET'])
def api_schema():
    return jsonify(API_SCHEMA)

@app.route('/', methods=['GET'])
def ui():
    return send_file('ui.html')

def determine_winner(local, opponent):
    # Returns result string from local player's perspective
    if local == opponent:
        return "draw"
    wins = {
        "rock": "scissors",
        "paper": "rock",
        "scissors": "paper"
    }
    if wins.get(local) == opponent:
        return "win"
    else:
        return "lose"

@app.route('/api/rps/play', methods=['POST'])
def play_move():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    player = data.get('player', 'local')
    if move not in ('rock', 'paper', 'scissors'):
        return jsonify({"status": "invalid move", "completed": game_state["completed"], "result": None}), 400
    if player == 'local':
        game_state["local_move"] = move
    else:
        game_state["opponent_move"] = move
    # Check if both moves are present
    if game_state["local_move"] and game_state["opponent_move"]:
        game_state["result"] = determine_winner(game_state["local_move"], game_state["opponent_move"])
        game_state["completed"] = "yes"
    else:
        game_state["completed"] = "no"
    game_state["last_update"] = time.time()
    response = {
        "status": "move recorded",
        "completed": game_state["completed"],
        "result": game_state["result"]
    }
    return jsonify(response)

@app.route('/api/status', methods=['GET'])
def status():
    response = {
        "local_move": game_state["local_move"],
        "opponent_move": game_state["opponent_move"],
        "completed": game_state["completed"],
        "result": game_state["result"]
    }
    return jsonify(response)

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
