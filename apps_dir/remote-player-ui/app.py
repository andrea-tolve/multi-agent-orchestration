import argparse
import json
import time
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

CONTAINER_PORT = 8000

# Internal game state
game_state = {
    "local_move": None,
    "opponent_move": None,
    "result": None,
    "completed": "no"
}

# Helper to compute RPS result
def compute_result(local, opponent):
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

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "remote-player-ui",
        "description": "Flask web service that Render a page with three buttons (rock, paper, scissors) and an area for the result. Accept a POST containing the chosen move, store it, accept the opponent's move via a separate endpoint, determine the outcome, and return the combined result to be shown on the page."
    },
    "paths": {
        "/api/rps/remote/play": {
            "post": {
                "summary": "Submit player's move",
                "description": "Accepts player's move (rock, paper, or scissors) and stores it.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {
                                        "type": "string",
                                        "description": "Player's move: rock, paper, or scissors"
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
                "description": "Accepts opponent's move forwarded from another agent.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {
                                        "type": "string",
                                        "description": "Opponent's move: rock, paper, or scissors"
                                    }
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Opponent move recorded",
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
                "summary": "Get current game status",
                "description": "Returns the stored moves and result, along with a completion flag.",
                "responses": {
                    "200": {
                        "description": "Current status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "object",
                                            "properties": {
                                                "local_move": {"type": "string"},
                                                "opponent_move": {"type": "string"},
                                                "result": {"type": "string"}
                                            }
                                        },
                                        "completed": {"type": "string", "description": "'yes' if both moves received, otherwise 'no'"}
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
                "summary": "Retrieve OpenAPI schema",
                "description": "Returns the OpenAPI 3.0.0 schema describing all endpoints.",
                "responses": {
                    "200": {
                        "description": "OpenAPI schema",
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

@app.route('/api/rps/remote/play', methods=['POST'])
def play_move():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if move not in ['rock', 'paper', 'scissors']:
        return jsonify({"message": "Invalid move"}), 400
    game_state['local_move'] = move
    # If opponent move already present, compute result
    if game_state['opponent_move']:
        game_state['result'] = compute_result(game_state['local_move'], game_state['opponent_move'])
        game_state['completed'] = "yes"
    else:
        game_state['completed'] = "no"
    return jsonify({"message": "Player move recorded"})

@app.route('/api/opponent-move', methods=['POST'])
def opponent_move():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if move not in ['rock', 'paper', 'scissors']:
        return jsonify({"message": "Invalid move"}), 400
    game_state['opponent_move'] = move
    if game_state['local_move']:
        game_state['result'] = compute_result(game_state['local_move'], game_state['opponent_move'])
        game_state['completed'] = "yes"
    else:
        game_state['completed'] = "no"
    return jsonify({"message": "Opponent move recorded"})

@app.route('/api/status', methods=['GET'])
def status():
    response = {
        "data": {
            "local_move": game_state.get('local_move'),
            "opponent_move": game_state.get('opponent_move'),
            "result": game_state.get('result')
        },
        "completed": game_state.get('completed', 'no')
    }
    return jsonify(response)

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
