
import argparse
import time
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

CONTAINER_PORT = 8000

# Internal game state
game_state = {
    "player_move": None,
    "opponent_move": None,
    "result": None,
    "completed": "no"
}

# Helper to determine game result
def determine_result(player, opponent):
    if player == opponent:
        return "draw"
    wins = {
        "rock": "scissors",
        "paper": "rock",
        "scissors": "paper"
    }
    if wins.get(player) == opponent:
        return "player wins"
    else:
        return "opponent wins"

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "local-player",
        "description": "Serve a web page containing three buttons (rock, paper, scissors). Accept the player's selection via POST, store it, and provide an endpoint that returns the opponent's move and the game outcome once both moves are received."
    },
    "paths": {
        "/api/rps/play": {
            "post": {
                "summary": "Submit player's move",
                "description": "Accepts the player's rock/paper/scissors selection and stores it.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "Player's move: rock, paper, or scissors"},
                                    "completed": {"type": "string", "description": "Optional flag indicating player has finished, e.g., 'yes' or 'no'"}
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Move stored successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string"},
                                        "message": {"type": "string"}
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
                "summary": "Get game status",
                "description": "Returns stored moves and whether the game is completed.",
                "responses": {
                    "200": {
                        "description": "Current game status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "object",
                                            "properties": {
                                                "player_move": {"type": "string"},
                                                "opponent_move": {"type": "string"},
                                                "result": {"type": "string"}
                                            }
                                        },
                                        "completed": {"type": "string"}
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
                "description": "Provides the OpenAPI specification for this service.",
                "responses": {
                    "200": {
                        "description": "OpenAPI spec",
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
                "summary": "Root UI",
                "description": "Serves the UI HTML page.",
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
    payload = request.get_json(silent=True) or {}
    move = payload.get('move')
    if move not in ('rock', 'paper', 'scissors'):
        return jsonify({"status": "error", "message": "Invalid move"}), 400
    game_state['player_move'] = move
    # Optional completed flag from client (not used for logic here)
    completed_flag = payload.get('completed')
    if completed_flag == 'yes':
        game_state['completed'] = 'yes'
    # If opponent move already present, compute result
    if game_state['opponent_move']:
        game_state['result'] = determine_result(game_state['player_move'], game_state['opponent_move'])
        game_state['completed'] = 'yes'
    return jsonify({"status": "success", "message": "Move recorded"})

@app.route('/api/status', methods=['GET'])
def status():
    data = {
        "player_move": game_state.get('player_move'),
        "opponent_move": game_state.get('opponent_move'),
        "result": game_state.get('result')
    }
    return jsonify({"data": data, "completed": game_state.get('completed', 'no')})

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
