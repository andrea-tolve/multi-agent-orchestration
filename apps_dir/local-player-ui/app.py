
import argparse
import threading
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Configuration constants
CONTAINER_PORT = 8000

# Global game state with thread safety
_game_state = {
    "local_move": None,
    "opponent_move": None,
    "result": None,
    "completed": "no"
}
_state_lock = threading.Lock()

def _determine_winner(local, opponent):
    # Returns "win", "lose", or "draw" from the perspective of the local player
    if local == opponent:
        return "draw"
    if (local == "rock" and opponent == "scissors") or \
       (local == "paper" and opponent == "rock") or \
       (local == "scissors" and opponent == "paper"):
        return "win"
    return "lose"

# API Schema definition - describes all endpoints and their contracts
API_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "local-player-ui",
        "description": "Render a page with three buttons (rock, paper, scissors) and an area for the result. Accept a POST containing the chosen move, store it, accept the opponent's move via a separate endpoint, determine the outcome, and return the combined result to be shown on the page."
    },
    "paths": {
        "/api/rps/local/play": {
            "post": {
                "summary": "Submit local player's move",
                "description": "Stores the chosen move for the local player.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "One of 'rock', 'paper', or 'scissors'."},
                                    "completed": {"type": "string", "description": "Optional flag indicating the player has finished their turn (yes/no)."}
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
                                        "message": {"type": "string"},
                                        "status": {"type": "string"}
                                    },
                                    "required": ["message", "status"]
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
                "description": "Stores the opponent's move. Does not compute the result until both moves are present.",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "move": {"type": "string", "description": "One of 'rock', 'paper', or 'scissors'."}
                                },
                                "required": ["move"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Opponent move stored",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string"},
                                        "status": {"type": "string"}
                                    },
                                    "required": ["message", "status"]
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
                "description": "Returns the result if both moves are present and a completion flag.",
                "responses": {
                    "200": {
                        "description": "Current status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"type": "string", "description": "Result of the game (win/lose/draw) or empty string if not ready."},
                                        "completed": {"type": "string", "description": "'yes' if game is completed, otherwise 'no'."}
                                    },
                                    "required": ["data", "completed"]
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
                "description": "Provides the OpenAPI 3.0.0 description of all endpoints.",
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
        },
        "/": {
            "get": {
                "summary": "Serve UI page",
                "description": "Returns the static HTML page containing the rock‑paper‑scissors UI.",
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

@app.route('/api-schema', methods=['GET'])
def api_schema():
    return jsonify(API_SCHEMA)

@app.route('/', methods=['GET'])
def ui():
    return send_file('ui.html')

@app.route('/api/rps/local/play', methods=['POST'])
def local_play():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if move not in ('rock', 'paper', 'scissors'):
        return jsonify({"message": "Invalid move", "status": "error"}), 400
    with _state_lock:
        _game_state['local_move'] = move
        # Reset previous result/completion if a new round starts
        _game_state['result'] = None
        _game_state['completed'] = 'no'
        # If opponent move already present, compute result
        if _game_state['opponent_move']:
            _game_state['result'] = _determine_winner(_game_state['local_move'], _game_state['opponent_move'])
            _game_state['completed'] = 'yes'
    return jsonify({"message": "Local move stored", "status": "yes"})

@app.route('/api/opponent-move', methods=['POST'])
def opponent_move():
    data = request.get_json(silent=True) or {}
    move = data.get('move')
    if move not in ('rock', 'paper', 'scissors'):
        return jsonify({"message": "Invalid move", "status": "error"}), 400
    with _state_lock:
        _game_state['opponent_move'] = move
        # If local move already present, compute result
        if _game_state['local_move']:
            _game_state['result'] = _determine_winner(_game_state['local_move'], _game_state['opponent_move'])
            _game_state['completed'] = 'yes'
    return jsonify({"message": "Opponent move stored", "status": "yes"})

@app.route('/api/status', methods=['GET'])
def status():
    with _state_lock:
        result = _game_state.get('result') or ""
        completed = _game_state.get('completed', 'no')
    return jsonify({"data": result, "completed": completed})

def main():
    parser = argparse.ArgumentParser(description="Run the generated service")
    parser.add_argument("--port", type=int, default=CONTAINER_PORT)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
