from flask import Flask, render_template_string, request, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import time
import logging
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ゲームルームの管理
game_rooms = {}

class Card:
    def __init__(self, value, suit, is_joker=False):
        self.value = value
        self.suit = suit
        self.is_joker = is_joker
        self.suits = ["♠", "♥", "♦", "♣"]
        self.values = [None, None, "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    
    def __str__(self):
        if self.is_joker:
            return "🃏"
        return f"{self.values[self.value]}{self.suits[self.suit]}"
    
    def get_value(self):
        return 'JOKER' if self.is_joker else self.value
    
    def to_dict(self):
        return {
            'value': self.value,
            'suit': self.suit,
            'is_joker': self.is_joker,
            'display': str(self)
        }

class GameRoom:
    def __init__(self, room_id):
        self.room_id = room_id
        self.players = {}
        self.current_player = 0
        self.game_phase = 'waiting'
        self.elimination_order = []
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.deck = []
        self.game_start_time = None
        self.turn_start_time = None
        self.game_history = []
        
    def add_player(self, player_id, name, sid):
        if len(self.players) >= 3:
            return False, "ルームが満員です（3人まで）"
        
        # 名前の重複チェック
        existing_names = [p['name'] for p in self.players.values()]
        if name in existing_names:
            return False, "同じ名前のプレーヤーが既に参加しています"
        
        used_positions = [p['position'] for p in self.players.values()]
        available_position = 0
        while available_position in used_positions:
            available_position += 1
        
        self.players[player_id] = {
            'name': name,
            'hand': [],
            'eliminated': False,
            'sid': sid,
            'position': available_position,
            'join_time': datetime.now(),
            'cards_drawn': 0,
            'pairs_discarded': 0
        }
        
        self.last_activity = datetime.now()
        logger.info(f"Player {name} joined room {self.room_id}")
        return True, "参加成功"
    
    def remove_player(self, player_id):
        if player_id in self.players:
            player_name = self.players[player_id]['name']
            del self.players[player_id]
            logger.info(f"Player {player_name} left room {self.room_id}")
            
            if len(self.players) < 3:
                self.reorganize_positions()
    
    def reorganize_positions(self):
        players_list = list(self.players.values())
        for i, player in enumerate(players_list):
            player['position'] = i
        if len(players_list) > 0:
            self.current_player = 0
        else:
            self.current_player = 0
    
    def create_deck(self):
        deck = []
        for value in range(2, 15):
            for suit in range(4):
                deck.append(Card(value, suit))
        deck.append(Card(0, 0, True))
        
        for i in range(len(deck)):
            j = random.randint(0, len(deck) - 1)
            deck[i], deck[j] = deck[j], deck[i]
        
        return deck
    
    def start_game(self):
        if len(self.players) != 3:
            return False
        
        self.deck = self.create_deck()
        player_list = list(self.players.values())
        
        for i, card in enumerate(self.deck):
            player_list[i % 3]['hand'].append(card)
        
        self.game_phase = 'discard'
        self.game_start_time = datetime.now()
        self.last_activity = datetime.now()
        
        logger.info(f"Game started in room {self.room_id} with players: {[p['name'] for p in player_list]}")
        
        return True
    
    def discard_pairs_for_player(self, player_data):
        new_hand = []
        card_groups = {}
        pairs_count = 0
        
        for card in player_data['hand']:
            if card.is_joker:
                new_hand.append(card)
            else:
                value = card.get_value()
                if value not in card_groups:
                    card_groups[value] = []
                card_groups[value].append(card)
        
        for value, cards in card_groups.items():
            if len(cards) >= 2:
                pairs = len(cards) // 2
                pairs_count += pairs
                remaining = len(cards) % 2
                for i in range(remaining):
                    new_hand.append(cards[i])
            else:
                new_hand.append(cards[0])
        
        player_data['hand'] = new_hand
        player_data['pairs_discarded'] += pairs_count
        return pairs_count
    
    def get_next_player_position(self, current_position):
        positions = [p['position'] for p in self.players.values() if not p['eliminated']]
        positions.sort()
        
        if current_position not in positions:
            return positions[0] if positions else 0
        
        current_index = positions.index(current_position)
        next_index = (current_index + 1) % len(positions)
        return positions[next_index]
    
    def get_player_by_position(self, position):
        for player_data in self.players.values():
            if player_data['position'] == position:
                return player_data
        return None
    
    def check_win_condition(self):
        active_players = [p for p in self.players.values() if not p['eliminated']]
        return len(active_players) <= 1
    
    def is_room_inactive(self, timeout_minutes=30):
        return datetime.now() - self.last_activity > timedelta(minutes=timeout_minutes)
    
    def add_to_history(self, action, player_name, details=None):
        self.game_history.append({
            'timestamp': datetime.now(),
            'action': action,
            'player': player_name,
            'details': details
        })
    
    def to_dict_for_player(self, player_id):
        player_data = self.players.get(player_id)
        if not player_data:
            return None
        
        my_info = {
            'name': player_data['name'],
            'hand': [card.to_dict() for card in player_data['hand']],
            'hand_count': len(player_data['hand']),
            'eliminated': player_data['eliminated'],
            'position': player_data['position'],
            'cards_drawn': player_data.get('cards_drawn', 0),
            'pairs_discarded': player_data.get('pairs_discarded', 0)
        }
        
        other_players = []
        for pid, pdata in self.players.items():
            if pid != player_id:
                other_players.append({
                    'name': pdata['name'],
                    'hand_count': len(pdata['hand']),
                    'eliminated': pdata['eliminated'],
                    'position': pdata['position']
                })
        
        return {
            'room_id': self.room_id,
            'my_info': my_info,
            'other_players': other_players,
            'current_player_position': self.current_player,
            'game_phase': self.game_phase,
            'elimination_order': self.elimination_order,
            'player_count': len(self.players),
            'game_start_time': self.game_start_time.isoformat() if self.game_start_time else None
        }

def cleanup_inactive_rooms():
    current_time = datetime.now()
    rooms_to_delete = []
    
    for room_id, room in game_rooms.items():
        if room.is_room_inactive():
            rooms_to_delete.append(room_id)
    
    for room_id in rooms_to_delete:
        logger.info(f"Cleaning up inactive room: {room_id}")
        del game_rooms[room_id]
    
    return len(rooms_to_delete)

@app.route('/')
def index():
    html_template = '''
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>マルチプレーヤー ババ抜き</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            min-height: 100vh;
            line-height: 1.6;
        }
        .container {
            background: rgba(255, 255, 255, 0.1);
            padding: 30px;
            border-radius: 20px;
            backdrop-filter: blur(15px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.5);
            font-size: 2.5em;
            background: linear-gradient(45deg, #ffd700, #ffed4e);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .setup {
            text-align: center;
            margin-bottom: 30px;
        }
        .input-group {
            margin: 20px 0;
            position: relative;
        }
        .input-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #ffd700;
        }
        input[type="text"] {
            padding: 15px 25px;
            border: none;
            border-radius: 30px;
            font-size: 16px;
            width: 300px;
            max-width: 100%;
            text-align: center;
            transition: all 0.3s ease;
            background: rgba(255, 255, 255, 0.9);
            color: #333;
        }
        input[type="text"]:focus {
            outline: none;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
            background: rgba(255, 255, 255, 1);
        }
        button {
            background: linear-gradient(45deg, #ff6b6b, #ee5a24);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 30px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin: 10px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }
        button:hover:not(:disabled) {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
            background: linear-gradient(45deg, #ff5252, #d63031);
        }
        button:active:not(:disabled) {
            transform: translateY(-1px);
        }
        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        .game-area {
            display: none;
        }
        .message {
            text-align: center;
            margin: 20px 0;
            padding: 20px;
            background: rgba(255, 255, 255, 0.15);
            border-radius: 15px;
            font-weight: 500;
            white-space: pre-line;
            border-left: 4px solid #ffd700;
            animation: slideIn 0.5s ease;
        }
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .room-info {
            background: rgba(255, 255, 255, 0.2);
            padding: 20px;
            border-radius: 15px;
            margin: 20px 0;
            text-align: center;
            font-weight: 600;
            border: 2px solid rgba(255, 215, 0, 0.3);
        }
        .my-hand {
            background: rgba(255, 215, 0, 0.2);
            padding: 25px;
            border-radius: 15px;
            margin: 25px 0;
            border: 2px solid rgba(255, 215, 0, 0.4);
        }
        .my-hand h3 {
            color: #ffd700;
            text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5);
        }
        .cards {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            justify-content: center;
            margin: 20px 0;
        }
        .card {
            background: linear-gradient(145deg, #ffffff, #f0f0f0);
            color: #333;
            padding: 12px;
            border-radius: 10px;
            min-width: 70px;
            text-align: center;
            font-weight: bold;
            font-size: 14px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
            transition: all 0.3s ease;
            border: 2px solid #ddd;
        }
        .card:hover {
            transform: translateY(-5px) scale(1.05);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.3);
        }
        .card.joker {
            background: linear-gradient(45deg, #ff6b6b, #ee5a24);
            color: white;
            border: 2px solid #c0392b;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); }
        }
        .other-players {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 25px 0;
        }
        .other-player {
            background: rgba(255, 255, 255, 0.15);
            padding: 20px;
            border-radius: 15px;
            text-align: center;
            transition: all 0.3s ease;
            border: 2px solid transparent;
        }
        .other-player:hover {
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }
        .current-turn {
            border: 3px solid #ffd700;
            box-shadow: 0 0 20px rgba(255, 215, 0, 0.6);
            background: rgba(255, 215, 0, 0.1);
            animation: glow 2s ease-in-out infinite alternate;
        }
        @keyframes glow {
            from { box-shadow: 0 0 20px rgba(255, 215, 0, 0.6); }
            to { box-shadow: 0 0 30px rgba(255, 215, 0, 0.9); }
        }
        .eliminated {
            opacity: 0.5;
            filter: grayscale(100%);
        }
        .connection-status {
            position: fixed;
            top: 15px;
            right: 15px;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            z-index: 1000;
            transition: all 0.3s ease;
        }
        .connected {
            background: linear-gradient(45deg, #00b894, #00a085);
            color: white;
            box-shadow: 0 2px 10px rgba(0, 184, 148, 0.3);
        }
        .disconnected {
            background: linear-gradient(45deg, #e17055, #d63031);
            color: white;
            box-shadow: 0 2px 10px rgba(214, 48, 49, 0.3);
        }
        .connecting {
            background: linear-gradient(45deg, #fdcb6e, #e17055);
            color: white;
            box-shadow: 0 2px 10px rgba(225, 112, 85, 0.3);
        }
        .card-back {
            background: linear-gradient(145deg, #4a90e2, #357abd);
            color: white;
            padding: 12px;
            border-radius: 10px;
            min-width: 70px;
            text-align: center;
            font-weight: bold;
            font-size: 14px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
            cursor: pointer;
            transition: all 0.3s ease;
            border: 2px solid #2980b9;
        }
        .card-back:hover {
            transform: translateY(-5px) scale(1.1);
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
            border: 3px solid #ffd700;
        }
        .card-back:active {
            transform: translateY(-2px) scale(1.05);
        }
        .game-order {
            background: rgba(255, 215, 0, 0.15);
            padding: 25px;
            border-radius: 20px;
            margin: 25px 0;
            border: 2px solid #ffd700;
            text-align: center;
            box-shadow: 0 4px 15px rgba(255, 215, 0, 0.2);
        }
        .game-order h3 {
            color: #ffd700;
            text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5);
            margin-bottom: 15px;
        }
        .stats {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 10px;
            margin: 15px 0;
            font-size: 14px;
            text-align: center;
        }
        .error-message {
            background: rgba(231, 76, 60, 0.2);
            border: 2px solid #e74c3c;
            color: #fff;
            padding: 15px;
            border-radius: 10px;
            margin: 15px 0;
            animation: shake 0.5s ease-in-out;
        }
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-5px); }
            75% { transform: translateX(5px); }
        }
        .success-message {
            background: rgba(46, 204, 113, 0.2);
            border: 2px solid #2ecc71;
            color: #fff;
            padding: 15px;
            border-radius: 10px;
            margin: 15px 0;
        }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .container { padding: 20px; }
            h1 { font-size: 2em; }
            input[type="text"] { width: 100%; }
            .other-players { grid-template-columns: 1fr; }
            .cards { gap: 8px; }
            .card, .card-back { min-width: 60px; padding: 8px; font-size: 12px; }
        }
    </style>
</head>
<body>
    <div class="connection-status" id="connectionStatus">接続中...</div>
    
    <div class="container">
        <h1>🎴 マルチプレーヤー ババ抜き 🎴</h1>
        
        <div id="setup" class="setup">
            <div class="input-group">
                <label for="playerName">プレーヤー名</label>
                <input type="text" id="playerName" placeholder="お名前をご入力ください" maxlength="20">
            </div>
            <div class="input-group">
                <label for="roomId">ルームID</label>
                <input type="text" id="roomId" placeholder="新規作成の場合は空白" maxlength="10">
            </div>
            <button type="button" id="joinButton">🚀 ゲームに参加する</button>
            <div class="stats">
                <small>💡 ルームIDを空白にすると新しいルームが作成されます</small>
            </div>
        </div>

        <div id="game" class="game-area">
            <div id="roomInfo" class="room-info"></div>
            <div id="message" class="message"></div>
            
            <div id="gameOrder" class="game-order" style="display: none;">
                <h3>🎮 ゲーム情報</h3>
                <div id="orderText"></div>
            </div>
            
            <div id="myHand" class="my-hand">
                <h3>🃏 あなたの手札</h3>
                <div id="myCards" class="cards"></div>
                <div id="myStats" class="stats"></div>
            </div>
            
            <div id="otherPlayers" class="other-players"></div>
            
            <div style="text-align: center; margin: 30px 0;">
                <button id="discardBtn" style="display: none;">🗑️ ペアを捨てる</button>
                <button id="startBtn" style="display: none;">🎮 ゲーム開始</button>
                <button onclick="leaveGame()">🚪 ゲーム退出</button>
            </div>
        </div>
    </div>

<script>
var socket = null;
var gameState = null;
var playerId = null;
var roomId = null;
var isConnected = false;
var lastClickTime = 0;
var clickDebounceMs = 500;

function showMessage(text, type) {
    var messageElement = document.getElementById('message');
    if (!messageElement) {
        console.warn('Message element not found');
        return;
    }
    
    if (typeof text !== 'string') {
        console.warn('Invalid message text:', text);
        text = String(text || '');
    }
    
    var safeText = text;
    var htmlText = safeText.replace(/\\n/g, '<br>');
    messageElement.innerHTML = htmlText;
    messageElement.style.display = 'block';
    
    messageElement.className = 'message';
    if (type === 'error') {
        messageElement.classList.add('error-message');
    } else if (type === 'success') {
        messageElement.classList.add('success-message');
    }
    
    if (type === 'success' && text.indexOf('参加しました') === -1) {
        setTimeout(function() {
            if (messageElement && messageElement.style.opacity !== '0.7') {
                messageElement.style.opacity = '0.7';
            }
        }, 5000);
    }
}

function debounceClick() {
    var now = Date.now();
    if (now - lastClickTime < clickDebounceMs) {
        return false;
    }
    lastClickTime = now;
    return true;
}

function validateInput() {
    var nameInput = document.getElementById('playerName');
    var joinButton = document.getElementById('joinButton');
    if (!nameInput || !joinButton) return;
    
    var isValid = nameInput.value.trim().length >= 2;
    joinButton.disabled = !isValid || !isConnected;
    
    if (nameInput.value.length > 0 && nameInput.value.length < 2) {
        nameInput.style.borderColor = '#e74c3c';
    } else {
        nameInput.style.borderColor = '';
    }
}

function generateRoomId() {
    var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    var result = '';
    for (var i = 0; i < 6; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
}

function generatePlayerId() {
    return 'player_' + Date.now() + '_' + Math.random().toString(36).substring(2, 10);
}

function formatTime(seconds) {
    var minutes = Math.floor(seconds / 60);
    var remainingSeconds = seconds % 60;
    return minutes + ':' + (remainingSeconds < 10 ? '0' : '') + remainingSeconds;
}

function updateConnectionStatus(status) {
    var statusElement = document.getElementById('connectionStatus');
    switch(status) {
        case 'connected':
            statusElement.textContent = '🟢 接続済み';
            statusElement.className = 'connection-status connected';
            isConnected = true;
            break;
        case 'disconnected':
            statusElement.textContent = '🔴 切断';
            statusElement.className = 'connection-status disconnected';
            isConnected = false;
            break;
        case 'connecting':
            statusElement.textContent = '🟡 接続中...';
            statusElement.className = 'connection-status connecting';
            isConnected = false;
            break;
    }
    validateInput();
}

function initializeSocket() {
    updateConnectionStatus('connecting');
    
    try {
        socket = io({
            transports: ['websocket', 'polling'],
            timeout: 10000,
            forceNew: true,
            autoConnect: true
        });
        
        setupSocketHandlers();
        
    } catch (error) {
        console.error('Socket.IO初期化エラー:', error);
        updateConnectionStatus('disconnected');
        showMessage('接続に失敗しました。ページを再読み込みしてください。', 'error');
    }
}

function setupSocketHandlers() {
    socket.on('connect', function() {
        console.log('Socket.IOに接続されました');
        updateConnectionStatus('connected');
    });

    socket.on('disconnect', function() {
        console.log('Socket.IOから切断されました');
        updateConnectionStatus('disconnected');
        showMessage('サーバーとの接続が切断されました。', 'error');
    });

    socket.on('connect_error', function(error) {
        console.error('Socket.IO接続エラー:', error);
        updateConnectionStatus('disconnected');
        showMessage('サーバーに接続できません。しばらく待ってから再試行してください。', 'error');
    });

    socket.on('game_joined', function(data) {
        console.log('game_joinedイベント受信:', data);
        
        var joinButton = document.getElementById('joinButton');
        joinButton.disabled = false;
        joinButton.textContent = '🚀 ゲームに参加する';
        
        if (data.success) {
            console.log('ゲーム参加成功');
            document.getElementById('setup').style.display = 'none';
            document.getElementById('game').style.display = 'block';
            updateGameDisplay(data.game_state);
            showMessage('ルーム「' + roomId + '」に参加しました！', 'success');
        } else {
            console.log('ゲーム参加失敗:', data.message);
            showMessage(data.message, 'error');
        }
    });

    socket.on('game_state_updated', function(data) {
        console.log('game_state_updatedイベント受信');
        updateGameDisplay(data);
    });

    socket.on('player_joined', function(data) {
        console.log('player_joinedイベント受信');
        showMessage(data.message, 'success');
        if (data.game_state) {
            updateGameDisplay(data.game_state);
        }
    });

    socket.on('message', function(data) {
        console.log('messageイベント受信');
        showMessage(data.message);
    });

    socket.on('error', function(data) {
        console.log('errorイベント受信');
        showMessage(data.message, 'error');
    });
}

function joinGame() {
    console.log('joinGame()関数が呼ばれました');
    
    if (!socket || !isConnected) {
        console.log('Socket.IOが接続されていません');
        showMessage('サーバーに接続中です。少しお待ちください...', 'error');
        
        setTimeout(function() {
            if (isConnected) {
                joinGame();
            } else {
                showMessage('サーバーに接続できません。ページを再読み込みしてください。', 'error');
            }
        }, 3000);
        return;
    }
    
    var name = document.getElementById('playerName').value.trim();
    var room = document.getElementById('roomId').value.trim();
    
    if (!name || name.length < 2) {
        showMessage('名前は2文字以上で入力してください', 'error');
        return;
    }
    
    if (name.length > 20) {
        showMessage('名前は20文字以内で入力してください', 'error');
        return;
    }
    
    if (!room) {
        room = generateRoomId();
        document.getElementById('roomId').value = room;
    }
    
    playerId = generatePlayerId();
    roomId = room;
    
    var button = document.getElementById('joinButton');
    button.disabled = true;
    button.textContent = '🔄 参加中...';
    
    try {
        socket.emit('join_game', {
            player_id: playerId,
            room_id: roomId,
            name: name
        });
        console.log('join_gameイベントを送信しました');
    } catch (error) {
        console.error('join_gameイベント送信エラー:', error);
        button.disabled = false;
        button.textContent = '🚀 ゲームに参加する';
        showMessage('参加に失敗しました。もう一度お試しください。', 'error');
    }
    
    setTimeout(function() {
        if (button.disabled && button.textContent === '🔄 参加中...') {
            button.disabled = false;
            button.textContent = '🚀 ゲームに参加する';
            showMessage('参加に時間がかかっています。もう一度お試しください。', 'error');
        }
    }, 15000);
}

function startGame() {
    if (!socket || !isConnected) {
        showMessage('サーバーに接続されていません', 'error');
        return;
    }
    
    if (!debounceClick()) {
        return;
    }
    
    socket.emit('start_game', {
        player_id: playerId,
        room_id: roomId
    });
}

function discardPairs() {
    if (!socket || !isConnected) {
        showMessage('サーバーに接続されていません', 'error');
        return;
    }
    
    if (!debounceClick()) {
        return;
    }
    
    socket.emit('discard_pairs', {
        player_id: playerId,
        room_id: roomId
    });
}

function drawCard(fromPosition, cardIndex) {
    if (!socket || !isConnected) {
        showMessage('サーバーに接続されていません', 'error');
        return;
    }
    
    if (!debounceClick()) {
        return;
    }
    
    socket.emit('draw_card', {
        player_id: playerId,
        room_id: roomId,
        from_position: fromPosition,
        card_index: cardIndex
    });
}

function leaveGame() {
    if (!confirm('本当にゲームから退出しますか？')) {
        return;
    }
    
    if (socket && isConnected && playerId && roomId) {
        socket.emit('leave_game', {
            player_id: playerId,
            room_id: roomId
        });
    }
    
    document.getElementById('setup').style.display = 'block';
    document.getElementById('game').style.display = 'none';
    
    var joinButton = document.getElementById('joinButton');
    joinButton.disabled = false;
    joinButton.textContent = '🚀 ゲームに参加する';
    
    document.getElementById('playerName').value = '';
    document.getElementById('roomId').value = '';
    
    showMessage('ゲームから退出しました。', 'success');
}

function updateGameDisplay(state) {
    if (!state) return;
    
    gameState = state;
    
    var roomInfo = document.getElementById('roomInfo');
    if (roomInfo) {
        var infoText = '🏠 ルームID: <strong>' + state.room_id + '</strong> | ';
        infoText += '👥 プレーヤー: ' + state.player_count + '/3人';
        if (state.game_start_time) {
            var startTime = new Date(state.game_start_time);
            var elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
            infoText += ' | ⏱️ 経過時間: ' + formatTime(elapsed);
        }
        roomInfo.innerHTML = infoText;
    }
    
    if (state.game_phase === 'waiting') {
        if (state.player_count < 3) {
            showMessage('プレーヤーを待機中... (' + state.player_count + '/3人)\\n\\n' +
                      '🔗 ルームID「' + state.room_id + '」を他のプレーヤーに教えてください！\\n' +
                      '💡 このIDを共有すれば、友達も参加できます。');
        } else {
            showGameOrderMessage(state);
        }
    }
    
    if (state.my_info) {
        updateMyHand(state.my_info);
    }
    
    if (state.other_players) {
        updateOtherPlayers(state.other_players, state.current_player_position, state.my_info ? state.my_info.position : 0);
    }
    
    updateButtons(state);
}

function showGameOrderMessage(state) {
    var allPlayers = [state.my_info];
    for (var i = 0; i < state.other_players.length; i++) {
        allPlayers.push(state.other_players[i]);
    }
    allPlayers.sort(function(a, b) { return a.position - b.position; });
    
    var myPosition = state.my_info.position;
    var nextPosition = (myPosition + 1) % 3;
    var targetPlayer = null;
    for (var i = 0; i < allPlayers.length; i++) {
        if (allPlayers[i].position === nextPosition) {
            targetPlayer = allPlayers[i];
            break;
        }
    }
    
    var orderMessage = '🎯 ゲーム準備完了！\\n\\n';
    orderMessage += '👥 参加プレーヤー:\\n';
    
    for (var i = 0; i < allPlayers.length; i++) {
        var player = allPlayers[i];
        if (player.position === myPosition) {
            orderMessage += '🌟 【' + player.name + '】 (あなた)\\n';
        } else {
            orderMessage += '👤 ' + player.name + '\\n';
        }
    }
    
    orderMessage += '\\n🎮 カードを引く順番:\\n';
    
    for (var i = 0; i < allPlayers.length; i++) {
        var player = allPlayers[i];
        var fromIndex = (i + 1) % 3;
        var fromPlayer = allPlayers[fromIndex];
        
        if (player.position === myPosition) {
            orderMessage += '🔹 【' + player.name + '】 が 【' + fromPlayer.name + '】 からカードを引く\\n';
        } else {
            orderMessage += '🔸 ' + player.name + ' が ' + fromPlayer.name + ' からカードを引く\\n';
        }
    }
    
    orderMessage += '\\n💡 あなたは 【' + targetPlayer.name + '】 からカードを引きます！\\n';
    orderMessage += '\\n✨ 準備ができたら「ゲーム開始」ボタンを押してください。';
    
    showMessage(orderMessage);
    
    var gameOrder = document.getElementById('gameOrder');
    var orderText = document.getElementById('orderText');
    if (gameOrder && orderText) {
        gameOrder.style.display = 'block';
        orderText.innerHTML = '🎯 あなたは 【' + targetPlayer.name + '】 からカードを引きます';
    }
}

function updateMyHand(myInfo) {
    var container = document.getElementById('myCards');
    var statsContainer = document.getElementById('myStats');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (myInfo.hand) {
        for (var i = 0; i < myInfo.hand.length; i++) {
            var card = myInfo.hand[i];
            var cardElement = document.createElement('div');
            cardElement.className = 'card';
            if (card.is_joker) {
                cardElement.classList.add('joker');
                cardElement.title = 'ジョーカー - ペアにならない特別なカード';
            } else {
                cardElement.title = card.display;
            }
            cardElement.textContent = card.display;
            container.appendChild(cardElement);
        }
    }
    
    if (statsContainer && myInfo.cards_drawn !== undefined) {
        var statsText = '📊 引いたカード: ' + myInfo.cards_drawn + '枚 | ';
        statsText += '🗑️ 捨てたペア: ' + myInfo.pairs_discarded + '組';
        statsContainer.innerHTML = statsText;
    }
}

function updateOtherPlayers(otherPlayers, currentPlayerPosition, myPosition) {
    var container = document.getElementById('otherPlayers');
    if (!container) return;
    
    container.innerHTML = '';
    
    for (var i = 0; i < otherPlayers.length; i++) {
        var player = otherPlayers[i];
        var playerDiv = document.createElement('div');
        playerDiv.className = 'other-player';
        
        if (player.eliminated) {
            playerDiv.classList.add('eliminated');
        }
        
        if (player.position === currentPlayerPosition) {
            playerDiv.classList.add('current-turn');
        }
        
        var cardsHtml = '';
        var canDrawFrom = gameState && gameState.game_phase === 'draw' && 
                         currentPlayerPosition === myPosition && 
                         isNextPlayer(myPosition, player.position) && 
                         !player.eliminated;
        
        if (canDrawFrom) {
            for (var j = 0; j < player.hand_count; j++) {
                cardsHtml += '<div class="card-back" onclick="drawCard(' + player.position + ', ' + j + ')" title="クリックしてカードを引く">🂠</div>';
            }
        } else {
            for (var j = 0; j < player.hand_count; j++) {
                cardsHtml += '<div class="card-back" style="opacity: 0.5; cursor: default;" title="引けません">🂠</div>';
            }
        }
        
        var statusText = player.eliminated ? ' (🏆 上がり)' : '';
        if (player.position === currentPlayerPosition && !player.eliminated) {
            statusText = ' (🎯 現在のターン)';
        }
        
        playerDiv.innerHTML = '<h4>👤 ' + player.name + statusText + '</h4>' +
                             '<p>🃏 手札: ' + player.hand_count + '枚</p>' +
                             '<div class="cards">' + cardsHtml + '</div>';
        
        container.appendChild(playerDiv);
    }
}

function isNextPlayer(myPosition, targetPosition) {
    if (!gameState) return false;
    
    var activePlayers = [gameState.my_info.position];
    for (var i = 0; i < gameState.other_players.length; i++) {
        var p = gameState.other_players[i];
        if (!p.eliminated) {
            activePlayers.push(p.position);
        }
    }
    activePlayers.sort();
    
    var myIndex = activePlayers.indexOf(myPosition);
    var nextIndex = (myIndex + 1) % activePlayers.length;
    
    return activePlayers[nextIndex] === targetPosition;
}

function updateButtons(state) {
    var startBtn = document.getElementById('startBtn');
    var discardBtn = document.getElementById('discardBtn');
    
    if (startBtn) {
        startBtn.style.display = 
            (state.game_phase === 'waiting' && state.player_count === 3) ? 'inline-block' : 'none';
        startBtn.onclick = startGame;
    }
    
    if (discardBtn) {
        discardBtn.style.display = 
            (state.game_phase === 'discard') ? 'inline-block' : 'none';
        discardBtn.onclick = discardPairs;
    }
}

document.addEventListener('DOMContentLoaded', function() {
    console.log('ページが読み込まれました');
    
    var joinButton = document.getElementById('joinButton');
    if (joinButton) {
        joinButton.addEventListener('click', function() {
            if (debounceClick()) {
                joinGame();
            }
        });
    }
    
    var playerNameInput = document.getElementById('playerName');
    var roomIdInput = document.getElementById('roomId');
    
    if (playerNameInput) {
        playerNameInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && debounceClick()) joinGame();
        });
        
        playerNameInput.addEventListener('input', function(e) {
            var value = e.target.value.trim();
            e.target.value = value;
            validateInput();
        });
    }
    
    if (roomIdInput) {
        roomIdInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && debounceClick()) joinGame();
        });
        
        roomIdInput.addEventListener('input', function(e) {
            var value = e.target.value.trim().toUpperCase();
            e.target.value = value.replace(/[^A-Z0-9]/g, '');
            validateInput();
        });
    }
    
    initializeSocket();
});

window.addEventListener('error', function(e) {
    console.error('JavaScript Error:', e.error);
    if (typeof showMessage === 'function') {
        showMessage('予期しないエラーが発生しました。ページを再読み込みしてください。', 'error');
    }
});

window.addEventListener('unhandledrejection', function(e) {
    console.error('Unhandled Promise Rejection:', e.reason);
    e.preventDefault();
});

document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible' && socket && !isConnected) {
        console.log('ページが表示されました。再接続を試行します。');
        initializeSocket();
    }
});

window.addEventListener('beforeunload', function(e) {
    if (socket && isConnected && playerId && roomId) {
        socket.emit('leave_game', {
            player_id: playerId,
            room_id: roomId
        });
    }
});
</script>
</body>
</html>
    '''
    return render_template_string(html_template)

@socketio.on('join_game')
def handle_join_game(data):
    player_id = data['player_id']
    room_id = data['room_id']
    name = data['name']
    
    if not name or len(name.strip()) < 2 or len(name.strip()) > 20:
        emit('game_joined', {
            'success': False,
            'message': '名前は2文字以上20文字以内で入力してください'
        })
        return
    
    if not room_id or len(room_id) > 10:
        emit('game_joined', {
            'success': False,
            'message': 'ルームIDは10文字以内で入力してください'
        })
        return
    
    name = name.strip()
    room_id = room_id.strip().upper()
    
    if room_id not in game_rooms:
        game_rooms[room_id] = GameRoom(room_id)
    
    room = game_rooms[room_id]
    
    result, message = room.add_player(player_id, name, request.sid)
    if result:
        join_room(room_id)
        session['player_id'] = player_id
        session['room_id'] = room_id
        
        emit('game_joined', {
            'success': True,
            'game_state': room.to_dict_for_player(player_id)
        })
        
        for pid in room.players:
            if pid != player_id:
                emit('player_joined', {
                    'message': f'🎉 {name}がゲームに参加しました！',
                    'game_state': room.to_dict_for_player(pid)
                }, room=room.players[pid]['sid'])
        
        room.add_to_history('player_joined', name)
        
    else:
        emit('game_joined', {
            'success': False,
            'message': message
        })

@socketio.on('start_game')
def handle_start_game(data):
    room_id = data['room_id']
    player_id = data['player_id']
    
    if room_id in game_rooms:
        room = game_rooms[room_id]
        
        if player_id not in room.players:
            emit('error', {'message': 'プレーヤーが見つかりません'})
            return
        
        if room.start_game():
            for pid in room.players:
                emit('game_state_updated', room.to_dict_for_player(pid), 
                     room=room.players[pid]['sid'])
            
            player_name = room.players[player_id]['name']
            room.add_to_history('game_started', player_name)
            emit('message', {'message': '🎮 ゲームが開始されました！まずはペアを捨ててください'}, room=room_id)
        else:
            emit('error', {'message': 'ゲームを開始できません（3人必要）'})

@socketio.on('discard_pairs')
def handle_discard_pairs(data):
    room_id = data['room_id']
    
    if room_id in game_rooms:
        room = game_rooms[room_id]
        
        total_pairs = 0
        for player_data in room.players.values():
            if not player_data['eliminated']:
                pairs_count = room.discard_pairs_for_player(player_data)
                total_pairs += pairs_count
        
        room.game_phase = 'draw'
        room.current_player = 0
        room.turn_start_time = datetime.now()
        
        for player_id in room.players:
            emit('game_state_updated', room.to_dict_for_player(player_id), 
                 room=room.players[player_id]['sid'])
        
        first_player = list(room.players.values())[0]['name']
        room.add_to_history('pairs_discarded', 'all_players', f'合計{total_pairs}組のペアを削除')
        emit('message', {'message': f'🗑️ 全員でペアを削除しました！\\n🎯 {first_player}からゲーム開始！隣のプレーヤーからカードを引いてください'}, room=room_id)

@socketio.on('draw_card')
def handle_draw_card(data):
    room_id = data['room_id']
    player_id = data['player_id']
    from_position = data['from_position']
    card_index = data['card_index']
    
    if room_id in game_rooms:
        room = game_rooms[room_id]
        
        current_player_data = room.players.get(player_id)
        if not current_player_data:
            emit('error', {'message': 'プレーヤーが見つかりません'})
            return
        
        if current_player_data['position'] != room.current_player:
            emit('error', {'message': 'あなたのターンではありません'})
            return
        
        from_player_data = room.get_player_by_position(from_position)
        if not from_player_data:
            emit('error', {'message': '対象プレーヤーが見つかりません'})
            return
        
        expected_next_position = room.get_next_player_position(room.current_player)
        if from_position != expected_next_position:
            emit('error', {'message': '引く順番が正しくありません'})
            return
        
        if card_index >= len(from_player_data['hand']):
            emit('error', {'message': '無効なカードです'})
            return
        
        drawn_card = from_player_data['hand'].pop(card_index)
        current_player_data['hand'].append(drawn_card)
        current_player_data['cards_drawn'] += 1
        
        if len(from_player_data['hand']) == 0:
            from_player_data['eliminated'] = True
            room.elimination_order.append(from_player_data['name'])
            room.add_to_history('player_eliminated', from_player_data['name'], 'カードがなくなり上がり')
        
        pairs_count = room.discard_pairs_for_player(current_player_data)
        
        if len(current_player_data['hand']) == 0:
            current_player_data['eliminated'] = True
            room.elimination_order.append(current_player_data['name'])
            room.add_to_history('player_eliminated', current_player_data['name'], 'ペア削除後に上がり')
        
        if room.check_win_condition():
            room.game_phase = 'finished'
            for pid in room.players:
                emit('game_state_updated', room.to_dict_for_player(pid), 
                     room=room.players[pid]['sid'])
            
            loser = [p for p in room.players.values() if not p['eliminated']][0]['name']
            room.add_to_history('game_finished', loser, 'ババを持って最下位')
            
            result_msg = f'🎉 ゲーム終了！\\n\\n'
            for i, player_name in enumerate(room.elimination_order):
                medal = '🥇' if i == 0 else '🥈' if i == 1 else '🥉'
                result_msg += f'{medal} {i+1}位: {player_name}\\n'
            result_msg += f'💀 3位: {loser} (ババ 🃏)\\n\\n'
            result_msg += '🎮 お疲れさまでした！'
            
            emit('message', {'message': result_msg}, room=room_id)
        else:
            room.current_player = room.get_next_player_position(room.current_player)
            room.turn_start_time = datetime.now()
            
            for pid in room.players:
                emit('game_state_updated', room.to_dict_for_player(pid), 
                     room=room.players[pid]['sid'])
            
            next_player_data = room.get_player_by_position(room.current_player)
            next_player_name = next_player_data['name'] if next_player_data else '不明'
            
            action_msg = f'🎯 {current_player_data["name"]}が{drawn_card}を引きました。'
            if pairs_count > 0:
                action_msg += f'\\n🗑️ {pairs_count}組のペアを削除！'
            action_msg += f'\\n\\n⏭️ 次は{next_player_name}のターンです！'
            
            room.add_to_history('card_drawn', current_player_data['name'], 
                              f'{drawn_card}を引き、{pairs_count}組のペアを削除')
            
            emit('message', {'message': action_msg}, room=room_id)

@socketio.on('leave_game')
def handle_leave_game(data):
    room_id = data.get('room_id')
    player_id = data.get('player_id')
    
    if room_id and room_id in game_rooms:
        room = game_rooms[room_id]
        player_data = room.players.get(player_id)
        player_name = player_data.get('name', '不明') if player_data else '不明'
        
        room.remove_player(player_id)
        leave_room(room_id)
        
        if len(room.players) == 0:
            logger.info(f"Empty room deleted: {room_id}")
            del game_rooms[room_id]
        else:
            if len(room.players) < 3:
                room.game_phase = 'waiting'
                room.current_player = 0
                room.elimination_order = []
                room.game_start_time = None
                
                for player in room.players.values():
                    player['hand'] = []
                    player['eliminated'] = False
                    player['cards_drawn'] = 0
                    player['pairs_discarded'] = 0
                
                for pid in room.players:
                    emit('game_state_updated', room.to_dict_for_player(pid), 
                         room=room.players[pid]['sid'])
                
                room.add_to_history('game_reset', player_name, '3人未満のためリセット')
                emit('message', {
                    'message': f'😢 {player_name}がゲームから退出しました。\\n3人未満になったため待機状態に戻ります。'
                }, room=room_id)
            else:
                if room.game_phase in ['discard', 'draw']:
                    room.game_phase = 'waiting'
                    room.current_player = 0
                    room.elimination_order = []
                    room.game_start_time = None
                    
                    for player in room.players.values():
                        player['hand'] = []
                        player['eliminated'] = False
                        player['cards_drawn'] = 0
                        player['pairs_discarded'] = 0
                
                for pid in room.players:
                    emit('game_state_updated', room.to_dict_for_player(pid), 
                         room=room.players[pid]['sid'])
                
                room.add_to_history('game_reset', player_name, 'プレーヤー退出によりリセット')
                emit('message', {
                    'message': f'😢 {player_name}がゲームから退出しました。\\nゲームをリセットします。'
                }, room=room_id)

@socketio.on('disconnect')
def handle_disconnect():
    player_id = session.get('player_id')
    room_id = session.get('room_id')
    
    if player_id and room_id and room_id in game_rooms:
        logger.info(f"Player {player_id} disconnected from room {room_id}")
        handle_leave_game({
            'player_id': player_id,
            'room_id': room_id
        })

@socketio.on_error_default
def default_error_handler(e):
    logger.error(f"SocketIO error: {e}")
    emit('error', {'message': 'サーバーエラーが発生しました。ページを再読み込みしてください。'})

def initialize():
    logger.info("ババ抜きゲームサーバーが起動しました")

def periodic_cleanup():
    while True:
        try:
            deleted_count = cleanup_inactive_rooms()
            if deleted_count > 0:
                logger.info(f"Periodic cleanup: removed {deleted_count} inactive rooms")
            time.sleep(1800)
        except Exception as e:
            logger.error(f"Periodic cleanup error: {e}")
            time.sleep(300)

import threading
cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    logger.info("Starting Babanuki Game Server...")
    socketio.run(app, debug=True, host='0.0.0.0', port=8000)