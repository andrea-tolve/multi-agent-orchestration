import argparse
import time
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Constants
CONTAINER_PORT = 8000

# In‑memory game state
game_state = {
    "player_move": None,
    "opponent_move": None,
    "result": None,
    "completed": False,
    "last_update": time.time()
}

# Helper to compute RPS result
def compute_result(player, opponent):
    if player == opponent:
        return "draw"
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    return "win" if wins[player] == opponent else "lose"

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "remote-rps-player",
        "description": "Flask web service that Serve a web page with three buttons (rock, paper, scissors). Accept a POST with the player's move, store it, accept a POST from the partner containing the opponent's move, and when both moves are present compute and return the result."
    },
    "paths": {
        "/api/rps/play": {
            "post": {
                "summary": "Submit player's move",
                "description": "Stores the player's move and, if the opponent's move is already present, computes the game result.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {
                                        "type": "string",
                                        "description": "Player's move: rock, paper, or scissors",
                                        "enum": ["rock", "paper", "scissors"]
                                    }
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
                                        "message": {"type": "string"}
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
                "description": "Stores the opponent's move and, if the player's move is already present, computes the game result.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {
                                        "type": "string",
                                        "description": "Opponent's move: rock, paper, or scissors",
                                        "enum": ["rock", "paper", "scissors"]
                                    }
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
                "summary": "Poll game status",
                "description": "Returns the current game data and whether the round is completed. No blocking; client should poll.",
                "responses": {
                    "200": {
                        "description": "Current status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": ["object", "null"],
                                            "properties": {
                                                "player_move": {"type": "string"},
                                                "opponent_move": {"type": "string"},
                                                "result": {"type": "string"}
                                            }
                                        },
                                        "completed": {"type": "string", "description": "'yes' if round finished, otherwise 'no'"}
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
def play_move():
    data = request.get_json(force=True)
    move = data.get('move')
    if move not in ['rock', 'paper', 'scissors']:
        return jsonify({"message": "Invalid move"}), 400
    game_state['player_move'] = move
    game_state['last_update'] = time.time()
    # If opponent already moved, compute result
    if game_state['opponent_move'] is not None:
        game_state['result'] = compute_result(game_state['player_move'], game_state['opponent_move'])
        game_state['completed'] = True
    return jsonify({"message": "Player move recorded"})

@app.route('/api/opponent-move', methods=['POST'])
def opponent_move():
    data = request.get_json(force=True)
    move = data.get('move')
    if move not in ['rock', 'paper', 'scissors']:
        return jsonify({"message": "Invalid move"}), 400
    game_state['opponent_move'] = move
    game_state['last_update'] = time.time()
    # If player already moved, compute result
    if game_state['player_move'] is not None:
        game_state['result'] = compute_result(game_state['player_move'], game_state['opponent_move'])
        game_state['completed'] = True
    return jsonify({"message": "Opponent move recorded"})

@app.route('/api/status', methods=['GET'])
def status():
    if game_state['completed']:
        data = {
            "player_move": game_state['player_move'],
            "opponent_move": game_state['opponent_move'],
            "result": game_state['result']
        }
        completed_str = "yes"
    else:
        data = None
        completed_str = "no"
    return jsonify({"data": data, "completed": completed_str})

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
