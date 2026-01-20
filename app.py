# app.py

# 1. IMPORTAÇÕES
import pandas as pd
import io
import sqlite3
import json
import os
# Linha alterada: Adicionado timedelta para cálculos de fuso horário
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, send_file
from collections import defaultdict

# 2. CONFIGURAÇÃO INICIAL DO FLASK
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static' # Adicionado para clareza
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Garante que as pastas de uploads e static existam
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True) # Garante que a pasta static exista

# 3. FUNÇÃO DE INICIALIZAÇÃO DO BANCO DE DADOS
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    # Tabela de Pedidos (com o novo campo 'status')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT,
            item_names TEXT NOT NULL,
            quantities TEXT NOT NULL,
            total REAL NOT NULL,
            order_number TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            called_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'preparing' -- Status: preparing, ready, completed
        )
    ''')
    # Tabela de Estoque
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            image_path TEXT,
            quantity INTEGER NOT NULL,
            detailed_description TEXT,
            is_available INTEGER NOT NULL DEFAULT 1, -- 1 for True, 0 for False
            is_promo INTEGER NOT NULL DEFAULT 0 -- 0 for False, 1 for True (Brinde)
        )
    ''')
    # Tabela de Avaliações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            score TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# 4. ROTAS DAS PÁGINAS PRINCIPAIS (HTML)
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/manager')
def manager():
    return render_template('manager.html')

@app.route('/monitor')
def monitor():
    return render_template('monitor.html')

@app.route('/kitchen')
def kitchen():
    return render_template('kitchen.html')

# 5. ROTAS DA API (JSON)
def generate_order_number():
    """
    Busca o último número de pedido no banco de dados e gera o próximo.
    Ex: Se o último for '005', retorna '006'. Se não houver, retorna '001'.
    """
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # Busca o último pedido ordenando pela data de criação
        cursor.execute("SELECT order_number FROM orders ORDER BY created_at DESC LIMIT 1")
        last_order = cursor.fetchone()
        
        if last_order and last_order[0]:
            # Se houver um último pedido, converte para inteiro, incrementa
            last_num = int(last_order[0])
            new_num = last_num + 1
        else:
            # Se for o primeiro pedido do dia (ou do banco)
            new_num = 1
            
        # Retorna o número formatado com 3 dígitos (ex: 001, 010, 123)
        return str(new_num).zfill(3)
        
    except Exception as e:
        print(f"Erro ao gerar número do pedido: {e}")
        # Fallback de emergência (não deve acontecer)
        return "1"
    finally:
        conn.close()

# --- API para Clientes e Pedidos ---
@app.route('/api/orders', methods=['POST'])
def add_order():
    data = request.get_json()
    
    # 1. Validação Básica
    customer_name = data.get('customer_name')
    phone = data.get('phone')
    items = data.get('items')
    total = data.get('total')
    payment_method = data.get('payment_method')

    # total is None verifica se a 'chave' total existe,
    # permitindo que o 'valor' 0 seja aceito.
    if not customer_name or not items or total is None or not payment_method:
        return jsonify({"error": "Dados do pedido incompletos."}), 400

    conn = None # Inicia conn como None
    try:
        # --- INÍCIO DAS LINHAS MOVIDAS ---
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        # Prepara os dados para o banco
        item_names = json.dumps([item['name'] for item in items])
        quantities = json.dumps([item['quantity'] for item in items])

        # Gera o número do pedido (AGORA DENTRO DO TRY)
        order_number = generate_order_number()

        initial_status = 'pending_payment' if payment_method == 'pix' else 'preparing'
        # --- FIM DAS LINHAS MOVIDAS ---
        
        # 2. Insere o Pedido
        cursor.execute('''
            INSERT INTO orders (customer_name, phone, item_names, quantities, total, order_number, payment_method, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (customer_name, phone, item_names, quantities, total, order_number, payment_method, initial_status))

        new_order_id = cursor.lastrowid
        
        # 3. Atualiza o Estoque
        for item in items:
            item_id = item['id']
            item_quantity = item['quantity']
            
            # Subtrai a quantidade vendida do estoque
            cursor.execute("UPDATE stock SET quantity = quantity - ? WHERE id = ?", (item_quantity, item_id))
            
            # Verificação de segurança (opcional, mas bom)
            if cursor.rowcount == 0:
                conn.rollback()
                return jsonify({"error": f"Item ID {item_id} não encontrado no estoque durante a finalização do pedido."}), 404

        conn.commit()
        return jsonify({
            "message": "Pedido salvo com sucesso!", 
            "order_number": order_number,
            "order_id": new_order_id 
        }), 201

    except Exception as e:
        if conn: # Só executa o rollback se a conexão foi estabelecida
            conn.rollback()
        print(f"Erro ao finalizar pedido e atualizar estoque: {e}")
        return jsonify({"error": f"Erro interno ao finalizar pedido. Detalhe: {e}"}), 500
    finally:
        if conn: # Só fecha a conexão se ela foi estabelecida
            conn.close()

@app.route('/api/orders/status', methods=['GET'])
def get_orders_by_status():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    try:
        # Busca pedidos em 'preparing' e 'ready'
        cursor.execute("SELECT order_number, customer_name, item_names, quantities, status FROM orders WHERE status IN ('preparing', 'ready') ORDER BY created_at ASC")
        orders_data = cursor.fetchall()
        
        preparing_orders = []
        ready_orders = []

        for row in orders_data:
            order_number, name, item_names_str, quantities_str, status = row
            
            # --- CORREÇÃO APLICADA AQUI: USAR json.loads de forma segura ---
            try:
                # O item_names e quantities devem ser lidos como JSON, pois foram salvos assim.
                item_names = json.loads(item_names_str)
                quantities = json.loads(quantities_str)
            except json.JSONDecodeError as e:
                # Se falhar a leitura do JSON, registra o erro e pula este pedido.
                print(f"Erro ao decodificar JSON do pedido #{order_number}: {e}")
                continue # Pula para o próximo pedido.
            # --- FIM DA CORREÇÃO ---

            items_list = []
            for i in range(len(item_names)):
                items_list.append(f"{quantities[i]}x {item_names[i]}")
            
            order = {
                "order_number": order_number,
                "customer_name": name,
                "items": items_list,
                "status": status
            }

            if status == 'preparing':
                preparing_orders.append(order)
            elif status == 'ready':
                ready_orders.append(order)

        return jsonify({"preparing": preparing_orders, "ready": ready_orders}), 200

    except Exception as e:
        print(f"Erro ao buscar pedidos por status: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/stock', methods=['GET'])
def get_public_stock():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # ALTERADO: Seleciona a nova coluna 'is_promo'
        cursor.execute("SELECT id, name, quantity, price, image_path, detailed_description, is_promo FROM stock WHERE is_available = 1")
        stock = cursor.fetchall()
        
        # ALTERADO: Lógica para modificar itens promocionais
        stock_list = []
        for item in stock:
            item_id, name, qty, price, img, desc, is_promo = item
            
            if is_promo == 1:
                # Se for brinde, muda o nome, preço e descrição para o cliente
                stock_list.append({
                    'id': item_id,
                    'name': 'Item Promocional',
                    'quantity': qty,
                    'price': 0.0,
                    'image_path': img,
                    'detailed_description': 'Um brinde especial da casa para você!'
                })
            else:
                # Item normal
                stock_list.append({
                    'id': item_id,
                    'name': name,
                    'quantity': qty,
                    'price': price,
                    'image_path': img,
                    'detailed_description': desc
                })
                
        return jsonify(stock_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/stock/check', methods=['POST'])
def check_stock():
    data = request.get_json()
    items_in_cart = data.get('items', [])
    if not items_in_cart:
        return jsonify({"error": "O carrinho está vazio."}), 400
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    unavailable_items = []
    
    try:
        for item in items_in_cart:
            cursor.execute("SELECT name, quantity FROM stock WHERE id = ?", (item['id'],))
            stock_item = cursor.fetchone() # Ex: ('Torta', 10) ou None

            # --- INÍCIO DA CORREÇÃO ---
            
            # 1. Verifica se o item NÃO FOI ENCONTRADO no banco de dados
            if not stock_item:
                unavailable_items.append({
                    "id": item['id'],
                    # Pega o nome do carrinho, já que ele não existe mais no BD
                    "name": item.get('name', 'Item removido'), 
                    "requested": item['quantity'],
                    "available": 0
                })
            # 2. Verifica se FOI ENCONTRADO, mas não tem estoque suficiente
            elif stock_item[1] < item['quantity']:
                unavailable_items.append({
                    "id": item['id'],
                    "name": stock_item[0], # Usa o nome do BD
                    "requested": item['quantity'],
                    "available": stock_item[1] # Informa o estoque real
                })
            
            # --- FIM DA CORREÇÃO ---

        if unavailable_items:
            return jsonify({"success": False, "message": "Estoque insuficiente.", "unavailable_items": unavailable_items}), 409
        else:
            return jsonify({"success": True}), 200
            
    except Exception as e:
        # Adiciona um log para você ver o erro no terminal do Python
        print(f"Erro inesperado em check_stock: {e}") 
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/score', methods=['POST'])
def save_score():
    data = request.get_json()
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # NOVO: Define o horário de Brasília (UTC-3)
    created_time_brt = datetime.utcnow() - timedelta(hours=3)
    
    try:
        # ALTERADO: Adicionado 'created_at' na inserção com o horário de Brasília
        cursor.execute("INSERT INTO scores (customer_name, score, created_at) VALUES (?, ?, ?)", (data.get('customer_name', 'Anônimo'), data['score'], created_time_brt))
        conn.commit()
        return jsonify({"message": "Score salvo!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- API para Administrador ---
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json()
    if data.get('adminId') == 'jjj' and data.get('adminPassword') == 'sinep':
        return jsonify({"success": True}), 200
    else:
        return jsonify({"success": False}), 401

@app.route('/api/admin/sales', methods=['GET'])
def get_sales():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, customer_name, item_names, quantities, total, order_number, created_at FROM orders ORDER BY created_at DESC")
        sales = cursor.fetchall()
        columns = ['id', 'Cliente', 'Itens', 'Quantidade', 'Total', 'Senha', 'Data/Hora']
        sales_list = [dict(zip(columns, sale)) for sale in sales]
        return jsonify(sales_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock', methods=['GET'])
def get_stock():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # ALTERADO: Seleciona a nova coluna 'is_promo' (agora 8 colunas)
        cursor.execute("SELECT id, name, quantity, price, image_path, detailed_description, is_available, is_promo FROM stock")
        stock = cursor.fetchall()
        
        # ALTERADO: Adiciona 'is_promo' (índice 7) ao dicionário
        stock_list = [{'id': i[0], 'name': i[1], 'quantity': i[2], 'price': i[3], 'image_path': i[4], 'detailed_description': i[5], 'is_available': i[6], 'is_promo': i[7]} for i in stock]
        
        return jsonify(stock_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock/add', methods=['POST'])
def add_new_stock():
    if 'image' not in request.files: return jsonify({"error": "Nenhum arquivo de imagem."}), 400
    file = request.files['image']
    data = request.form
    
    # --- INÍCIO DA CORREÇÃO ---
    
    # 1. Verifica o status 'is_promo' PRIMEIRO.
    is_promo = data.get('is_promo') == 'true'

    # 2. Cria a lista de campos obrigatórios básicos.
    required_fields = [data.get('name'), data.get('quantity'), data.get('detailed_description')]
    
    # 3. SÓ exija o campo 'price' se NÃO for um brinde.
    if not is_promo:
        required_fields.append(data.get('price'))

    # 4. Valida a lista de campos e o nome do arquivo.
    if file.filename == '' or not all(required_fields):
        return jsonify({"error": "Todos os campos são obrigatórios."}), 400
    
    # --- FIM DA CORREÇÃO ---

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)
    image_url = url_for('uploaded_file', filename=file.filename, _external=False)
    
    # Esta lógica de 'price_val' e 'is_promo_val' já estava correta e é mantida.
    is_promo_val = 1 if is_promo else 0
    price_val = 0.0 if is_promo_val == 1 else float(data['price'])
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO stock (name, quantity, price, image_path, detailed_description, is_promo) VALUES (?, ?, ?, ?, ?, ?)",
            (data['name'], int(data['quantity']), price_val, image_url, data['detailed_description'], is_promo_val)
        )
        conn.commit()
        return jsonify({"message": "Item adicionado com sucesso!"}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock/replenish', methods=['POST'])
def replenish_stock():
    data = request.json
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE stock SET quantity = quantity + ? WHERE id = ?", (data['quantity'], data['id']))
        conn.commit()
        if cursor.rowcount == 0: return jsonify({"message": "Item não encontrado."}), 404
        return jsonify({"message": "Estoque atualizado!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock/<int:item_id>', methods=['DELETE'])
def delete_stock_item(item_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM stock WHERE id = ?", (item_id,))
        conn.commit()
        if cursor.rowcount == 0: return jsonify({"message": "Item não encontrado."}), 404
        return jsonify({"message": "Item excluído!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
        
@app.route('/api/admin/sales/<int:sale_id>', methods=['DELETE'])
def delete_sale(sale_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM orders WHERE id = ?", (sale_id,))
        conn.commit()
        if cursor.rowcount == 0: return jsonify({"message": "Pedido não encontrado."}), 404
        return jsonify({"message": "Pedido excluído!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/sales/analysis', methods=['GET'])
def get_sales_analysis():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT item_names, quantities FROM orders")
        orders = cursor.fetchall()
        item_sales = defaultdict(int)
        
        for names_json, quantities_json in orders:
            try:
                # CORREÇÃO: Usar json.loads para decodificar as strings JSON
                item_names_list = json.loads(names_json)
                item_quantities_list = json.loads(quantities_json)
                
                # Garante que as quantidades sejam inteiros
                item_quantities_list = [int(q) for q in item_quantities_list]
                
                for name, quantity in zip(item_names_list, item_quantities_list):
                    item_sales[name] += quantity
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                # Continua mesmo se um pedido antigo tiver formato inválido
                print(f"Erro ao analisar dados de vendas (pulando pedido): {e}")
                continue

        if not item_sales: return jsonify({"most_sold": None, "least_sold": None, "sales_data": []}), 200
        
        most_sold_item = max(item_sales.items(), key=lambda item: item[1])
        least_sold_item = min(item_sales.items(), key=lambda item: item[1])
        sales_data = [{"name": name, "quantity": quantity} for name, quantity in item_sales.items()]
        
        return jsonify({"most_sold": {"name": most_sold_item[0], "quantity": most_sold_item[1]}, "least_sold": {"name": least_sold_item[0], "quantity": least_sold_item[1]}, "sales_data": sales_data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/sales/export', methods=['GET'])
def export_sales_to_excel():
    conn = sqlite3.connect('database.db')
    try:
        df = pd.read_sql_query("SELECT id as 'ID', order_number as 'Senha', customer_name as 'Cliente', item_names as 'Itens', quantities as 'Quantidades', total as 'Total (R$)', payment_method as 'Pagamento', created_at as 'Data/Hora' FROM orders ORDER BY created_at DESC", conn)
        df['Data/Hora'] = pd.to_datetime(df['Data/Hora']).dt.strftime('%d/%m/%Y %H:%M:%S')
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Vendas')
        output.seek(0)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheet.sheet', as_attachment=True, download_name=f"relatorio_vendas_{timestamp}.xlsx")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ROTA ADICIONADA PARA CORRIGIR O ERRO
@app.route('/api/admin/orders/reset', methods=['POST'])
def reset_orders():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # Apaga todos os registros da tabela de pedidos
        cursor.execute("DELETE FROM orders")
        # Opcional, mas recomendado: Reseta o contador de autoincremento do SQLite
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='orders'")
        conn.commit()
        return jsonify({"message": "Senhas e vendas reiniciadas com sucesso!"}), 200
    except Exception as e:
        conn.rollback()
        # Log do erro no servidor para depuração
        print(f"Erro ao reiniciar senhas: {e}")
        return jsonify({"error": "Ocorreu um erro interno no servidor."}), 500
    finally:
        conn.close()

# --- API para a Cozinha ---
@app.route('/api/kitchen/orders', methods=['GET'])
def get_kitchen_orders():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, order_number, customer_name, item_names, quantities FROM orders WHERE status = 'preparing' ORDER BY created_at ASC")
        
        orders = []
        for r in cursor.fetchall():
            try:
                # CORREÇÃO: Usar json.loads para decodificar as strings JSON
                items_list = json.loads(r[3])
                quantities_list = json.loads(r[4])
                
                orders.append({
                    "id": r[0],
                    "order_number": r[1],
                    "customer_name": r[2],
                    "items": items_list,       # Agora é uma lista Python
                    "quantities": quantities_list # Agora é uma lista Python
                })
            except json.JSONDecodeError as e:
                print(f"Erro ao decodificar JSON do pedido {r[1]} da cozinha: {e}")
                continue # Pula este pedido se o JSON estiver mal formatado

        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/kitchen/order/ready/<int:order_id>', methods=['POST'])
def mark_order_as_ready(order_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE orders SET status = 'ready' WHERE id = ?", (order_id,))
        conn.commit()
        if cursor.rowcount == 0: return jsonify({"error": "Pedido não encontrado"}), 404
        return jsonify({"message": "Pedido marcado como pronto!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- API para Gerente e Monitor (com sistema de status) ---
@app.route('/api/monitor/orders', methods=['GET'])
def get_monitor_orders():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT order_number, customer_name, status FROM orders WHERE status IN ('preparing', 'ready') ORDER BY created_at ASC")
        preparing = [{"order": r[0], "name": r[1], "status": r[2]} for r in cursor.fetchall()]
        
        cursor.execute("SELECT order_number, customer_name FROM orders WHERE status = 'completed' ORDER BY called_at DESC LIMIT 6")
        ready = [{"order": r[0], "name": r[1]} for r in cursor.fetchall()]
        
        return jsonify({"preparing": preparing, "ready": ready}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/manager/next_order', methods=['POST'])
def get_next_order():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, customer_name, order_number FROM orders WHERE status = 'ready' ORDER BY created_at ASC LIMIT 1")
        order = cursor.fetchone()
        if order:
            order_id, customer_name, order_number = order
            # NOVO: Define o horário de Brasília (UTC-3) para a chamada
            called_time_brt = datetime.utcnow() - timedelta(hours=3)
            # ALTERADO: Usa a variável com o horário de Brasília para atualizar 'called_at'
            cursor.execute("UPDATE orders SET status = 'completed', called_at = ? WHERE id = ?", (called_time_brt, order_id))
            conn.commit()
            return jsonify({"success": True, "customer_name": customer_name, "order_number": order_number}), 200
        else:
            return jsonify({"success": False, "message": "Nenhum pedido pronto para chamar."}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
        
@app.route('/api/manager/ready-orders-count', methods=['GET'])
def get_ready_orders_count():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(id) FROM orders WHERE status = 'ready'")
        count = cursor.fetchone()[0]
        return jsonify({"ready_count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock/update', methods=['POST'])
def update_stock_item():
    data = request.json
    item_id = data.get('id')
    new_price = data.get('price')
    new_quantity = data.get('quantity')

    if not all([item_id, new_price is not None, new_quantity is not None]):
        return jsonify({"error": "ID, preço e quantidade são obrigatórios."}), 400

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE stock SET price = ?, quantity = ? WHERE id = ?",
                       (float(new_price), int(new_quantity), int(item_id)))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"message": "Item não encontrado."}), 404
        return jsonify({"message": "Item atualizado com sucesso!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/stock/toggle_availability/<int:item_id>', methods=['POST'])
def toggle_availability(item_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # Inverte o valor atual (se for 1, vira 0; se for 0, vira 1)
        cursor.execute("UPDATE stock SET is_available = 1 - is_available WHERE id = ?", (item_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Item não encontrado."}), 404
        return jsonify({"message": "Status do item alterado com sucesso."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ROTA ADICIONADA PARA SERVIR ARQUIVOS ESTÁTICOS (SOM)
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(STATIC_FOLDER, filename)

@app.route('/api/admin/pending_payments', methods=['GET'])
def get_pending_payments():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, order_number, customer_name, total, created_at FROM orders WHERE status = 'pending_payment' ORDER BY created_at DESC")
        pending = [{"id": r[0], "order_number": r[1], "customer_name": r[2], "total": r[3], "created_at": r[4]} for r in cursor.fetchall()]
        return jsonify(pending), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# Rota para o ADMIN aprovar o pagamento
@app.route('/api/admin/approve_payment/<int:order_id>', methods=['POST'])
def approve_payment(order_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE orders SET status = 'preparing' WHERE id = ?", (order_id,))
        conn.commit()
        return jsonify({"message": "Pagamento aprovado! Pedido enviado para a cozinha."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# Rota para o ADMIN RECUSAR o pagamento (Devolve itens ao estoque)
@app.route('/api/admin/reject_payment/<int:order_id>', methods=['POST'])
def reject_payment(order_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        # 1. Busca os itens do pedido para devolver ao estoque
        cursor.execute("SELECT item_names, quantities FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            return jsonify({"error": "Pedido não encontrado"}), 404

        item_names_json, quantities_json = order
        
        # Decodifica os JSONs
        item_names = json.loads(item_names_json)
        quantities = json.loads(quantities_json)

        # 2. Devolve cada item ao estoque
        # Nota: Estamos buscando pelo NOME porque o ID não foi salvo no JSON original do pedido, 
        # mas o ideal seria salvar o ID. Como seu sistema salva nomes, vamos pelo nome.
        for name, qty in zip(item_names, quantities):
            cursor.execute("UPDATE stock SET quantity = quantity + ? WHERE name = ?", (qty, name))

        # 3. Atualiza o status do pedido para 'rejected'
        cursor.execute("UPDATE orders SET status = 'rejected' WHERE id = ?", (order_id,))
        
        conn.commit()
        return jsonify({"message": "Pagamento recusado e itens devolvidos ao estoque."}), 200
    except Exception as e:
        conn.rollback()
        print(f"Erro ao recusar pedido: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()       

# Rota para o CLIENTE verificar se foi aprovado (Polling)
@app.route('/api/orders/check_status/<int:order_id>', methods=['GET'])
def check_order_status_api(order_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        if row:
            return jsonify({"status": row[0]}), 200
        return jsonify({"status": "unknown"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# 6. INICIALIZAÇÃO DO SERVIDOR
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)