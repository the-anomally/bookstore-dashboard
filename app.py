import csv
from datetime import date, timedelta
import hmac
import io
import psycopg
from psycopg import sql
import streamlit as st

st.set_page_config(page_title="Bookstore Cloud Portal", layout="wide")

DB_URL = st.secrets["DB_URL"]


def fetch_table(query, params=None):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    return rows, [dict(zip(columns, row)) for row in rows]


def to_csv_bytes(dict_rows):
    if not dict_rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(dict_rows[0].keys()))
    writer.writeheader()
    writer.writerows(dict_rows)
    return output.getvalue().encode("utf-8")


# Ensure schema extensions, reservations table, and late-fee settings exist
with psycopg.connect(DB_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE rentals ADD COLUMN IF NOT EXISTS return_date DATE;")
        cur.execute("ALTER TABLE rentals ADD COLUMN IF NOT EXISTS due_date DATE;")
        cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS stock INTEGER NOT NULL DEFAULT 1;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id SERIAL PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(book_id),
                customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
                reserved_date DATE NOT NULL DEFAULT CURRENT_DATE,
                status TEXT NOT NULL DEFAULT 'Pending'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bookstore_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                daily_late_fee NUMERIC(10, 2) NOT NULL DEFAULT 50.00,
                currency TEXT NOT NULL DEFAULT 'KES'
            );
            """
        )
        cur.execute(
            """
            INSERT INTO bookstore_settings (id, daily_late_fee, currency)
            VALUES (1, 50.00, 'KES')
            ON CONFLICT (id) DO NOTHING;
            """
        )


def get_editable_columns(table_name, id_col):
    col_rows, _ = fetch_table(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name != %s
          AND (column_default IS NULL OR column_name = 'stock')
        ORDER BY ordinal_position;
        """,
        (table_name, id_col),
    )
    return col_rows


def render_field_input(col_name, data_type, current_val=None):
    label = col_name.replace("_", " ").title()
    if data_type in ("integer", "bigint", "smallint"):
        default_int = int(current_val) if current_val is not None else (1 if col_name == "stock" else 0)
        return st.number_input(label, step=1, min_value=0 if col_name == "stock" else None, value=default_int)
    elif data_type in ("numeric", "decimal", "real", "double precision"):
        default_float = float(current_val) if current_val is not None else 0.0
        return st.number_input(label, step=0.01, value=default_float)
    elif data_type == "date":
        default_date = current_val if current_val is not None else date.today()
        return st.date_input(label, value=default_date)
    elif data_type == "boolean":
        default_bool = bool(current_val) if current_val is not None else False
        return st.checkbox(label, value=default_bool)
    else:
        default_str = str(current_val) if current_val is not None else ""
        text_val = st.text_input(label, value=default_str)
        return text_val.strip() if text_val.strip() else None


def render_add_row_form(table_name, id_col, form_key, button_label):
    col_rows = get_editable_columns(table_name, id_col)
    with st.expander(f"+ {button_label}"):
        with st.form(form_key, clear_on_submit=True):
            values = {
                col_name: render_field_input(col_name, data_type)
                for col_name, data_type in col_rows
            }
            if st.form_submit_button(button_label):
                try:
                    cols = list(values.keys())
                    query = sql.SQL("INSERT INTO {} ({}) VALUES ({});").format(
                        sql.Identifier(table_name),
                        sql.SQL(", ").join(map(sql.Identifier, cols)),
                        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                    )
                    with psycopg.connect(DB_URL) as conn:
                        with conn.cursor() as cur:
                            cur.execute(query, [values[c] for c in cols])
                    st.success(f"{button_label} successful!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save record: {e}")


# --- DUAL-ROLE LOGIN GATE (STAFF vs CUSTOMER) ---
if "role" not in st.session_state:
    st.session_state["role"] = None
if "customer_user" not in st.session_state:
    st.session_state["customer_user"] = None


def require_login():
    if st.session_state["role"] in ("staff", "customer"):
        return True

    st.title("Bookstore Cloud Portal")
    tab_staff_login, tab_cust_login = st.tabs(["Staff Login", "Customer Login"])

    with tab_staff_login:
        with st.form("staff_login_form"):
            entered_password = st.text_input("Staff Password", type="password")
            if st.form_submit_button("Log In as Staff"):
                if hmac.compare_digest(entered_password, st.secrets["STAFF_PASSWORD"]):
                    st.session_state["role"] = "staff"
                    st.rerun()
                else:
                    st.error("Incorrect staff password.")

    with tab_cust_login:
        customer_pass = st.secrets.get("CUSTOMER_PASSWORD", "bookstore123")
        with st.form("customer_login_form"):
            cust_identifier = st.text_input("Your Full Name or Customer ID").strip()
            entered_cust_pass = st.text_input("Customer Portal Password", type="password")
            if st.form_submit_button("Log In as Customer"):
                if not cust_identifier:
                    st.warning("Please enter your name or Customer ID.")
                elif not hmac.compare_digest(entered_cust_pass, customer_pass):
                    st.error("Incorrect customer portal password.")
                else:
                    cust_rows, _ = fetch_table("SELECT * FROM customers ORDER BY customer_id;")
                    matched_customer = next(
                        (
                            r
                            for r in cust_rows
                            if str(r[0]) == cust_identifier
                            or str(r[1]).strip().lower() == cust_identifier.lower()
                        ),
                        None,
                    )
                    if matched_customer:
                        st.session_state["role"] = "customer"
                        st.session_state["customer_user"] = {
                            "id": matched_customer[0],
                            "name": matched_customer[1],
                        }
                        st.rerun()
                    else:
                        st.error("Customer not found. Check your spelling or register below.")

        st.write("")
        render_add_row_form(
            "customers",
            "customer_id",
            "self_register_customer",
            "New Customer? Register Here",
        )

    return False


if not require_login():
    st.stop()


def calculate_late_fee(return_date, due_date, daily_rate):
    if due_date is None:
        return 0.0
    effective_end = return_date if return_date is not None else date.today()
    if effective_end > due_date:
        days_late = (effective_end - due_date).days
        return round(days_late * float(daily_rate), 2)
    return 0.0


def format_rental_status(return_date, due_date):
    today = date.today()
    if return_date is not None:
        return f"Returned ({return_date})"
    if due_date is None:
        return "Active"
    if due_date < today:
        days_late = (today - due_date).days
        return f"OVERDUE ({days_late} day{'s' if days_late != 1 else ''} late)"
    if due_date == today:
        return "Due Today"
    days_left = (due_date - today).days
    return f"Active ({days_left} day{'s' if days_left != 1 else ''} left)"


# Load shared data and store settings
settings_rows, _ = fetch_table("SELECT daily_late_fee, currency FROM bookstore_settings WHERE id = 1;")
daily_late_fee = float(settings_rows[0][0]) if settings_rows else 50.00
currency = settings_rows[0][1] if settings_rows else "KES"

book_rows, books_data = fetch_table("SELECT * FROM books ORDER BY book_id;")
customer_rows, customers_data = fetch_table("SELECT * FROM customers ORDER BY customer_id;")
all_rentals, _ = fetch_table(
    """
    SELECT rental_id, book_id, customer_id, borrow_date, return_date, due_date
    FROM rentals
    ORDER BY rental_id DESC;
    """
)
all_reservations, _ = fetch_table(
    """
    SELECT reservation_id, book_id, customer_id, reserved_date, status
    FROM reservations
    ORDER BY reservation_id DESC;
    """
)

book_names = {r[0]: r[1] for r in book_rows}
customer_names = {r[0]: r[1] for r in customer_rows}
today = date.today()

pending_reservations = [res for res in all_reservations if res[4] == "Pending"]
pending_res_by_book = {}
for res in pending_reservations:
    pending_res_by_book[res[1]] = pending_res_by_book.get(res[1], 0) + 1

# ==============================================================================
# VIEW A: CUSTOMER PORTAL
# ==============================================================================
if st.session_state["role"] == "customer":
    cust = st.session_state["customer_user"]
    col_c_title, col_c_logout = st.columns([6, 1])
    with col_c_title:
        st.title(f"Welcome, {cust['name']} (ID #{cust['id']})")
    with col_c_logout:
        st.write("")
        if st.button("Log Out"):
            st.session_state["role"] = None
            st.session_state["customer_user"] = None
            st.rerun()

    my_rentals = [r for r in all_rentals if r[2] == cust["id"]]
    my_active = [r for r in my_rentals if r[4] is None]
    my_overdue = [r for r in my_active if r[5] is not None and r[5] < today]
    my_total_fees = sum(calculate_late_fee(r[4], r[5], daily_late_fee) for r in my_active)
    my_reservations = [res for res in all_reservations if res[2] == cust["id"]]
    my_pending_book_ids = {res[1] for res in my_reservations if res[4] == "Pending"}

    if my_overdue:
        overdue_titles = ", ".join(
            f"{book_names.get(r[1], f'Book #{r[1]}')} (due {r[5]})"
            for r in my_overdue
        )
        st.error(
            f"Overdue Notice: Please return {overdue_titles}. "
            f"Current accrued late fees: {currency} {my_total_fees:,.2f} ({currency} {daily_late_fee:,.2f}/day)."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("My Active Rentals", len(my_active))
    c2.metric("My Overdue Books", len(my_overdue))
    c3.metric("Accrued Late Fees", f"{currency} {my_total_fees:,.2f}")
    c4.metric("Pending Reservations", len(my_pending_book_ids))

    st.divider()
    tab_my_rentals, tab_catalog, tab_my_res = st.tabs(
        ["My Borrowed Books & Due Dates", "Browse & Reserve Books", "My Reservations"]
    )

    with tab_my_rentals:
        if my_rentals:
            my_table = [
                {
                    "Rental ID": r[0],
                    "Book Title": book_names.get(r[1], f"Book #{r[1]}"),
                    "Borrow Date": r[3],
                    "Due Date": r[5] if r[5] else "Not set",
                    "Status": format_rental_status(r[4], r[5]),
                    f"Late Fee ({currency})": f"{calculate_late_fee(r[4], r[5], daily_late_fee):,.2f}",
                }
                for r in my_rentals
            ]
            st.dataframe(my_table, hide_index=True, use_container_width=True)
        else:
            st.info("You have no rental history yet.")

    with tab_catalog:
        reservable_books = []
        catalog_rows = []
        for raw, row_dict in zip(book_rows, books_data):
            book_id = row_dict["book_id"]
            physical_stock = row_dict.get("stock", 0)
            reserved_holds = pending_res_by_book.get(book_id, 0)
            net_available = max(0, physical_stock - reserved_holds)

            clean_row = dict(row_dict)
            clean_row["Reserved Holds"] = reserved_holds
            clean_row["Availability"] = (
                f"Available ({net_available} ready to reserve)"
                if net_available > 0
                else "Unavailable / All Copies Held"
            )
            catalog_rows.append(clean_row)
            if net_available > 0 and book_id not in my_pending_book_ids:
                reservable_books.append((raw, net_available))

        if reservable_books:
            with st.form("customer_reserve_form", clear_on_submit=True):
                chosen_res = st.selectbox(
                    "Reserve a Copy for Pickup",
                    reservable_books,
                    format_func=lambda item: f"{item[0][0]} - {item[0][1]} ({item[1]} available)",
                )
                if st.form_submit_button("Reserve Copy"):
                    with psycopg.connect(DB_URL) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO reservations (book_id, customer_id, reserved_date, status) VALUES (%s, %s, %s, 'Pending');",
                                (chosen_res[0][0], cust["id"], today),
                            )
                    st.rerun()
        else:
            st.info("No additional books are currently available to reserve.")

        catalog_search = (
            st.text_input("Search Book Catalog", placeholder="Search by title or details...")
            .strip()
            .lower()
        )
        filtered_catalog = [
            row
            for row in catalog_rows
            if not catalog_search or any(catalog_search in str(v).lower() for v in row.values())
        ]
        st.dataframe(filtered_catalog, hide_index=True, use_container_width=True)

    with tab_my_res:
        if my_reservations:
            res_table = [
                {
                    "Reservation ID": res[0],
                    "Book Title": book_names.get(res[1], f"Book #{res[1]}"),
                    "Reserved Date": res[3],
                    "Status": res[4],
                }
                for res in my_reservations
            ]
            st.dataframe(res_table, hide_index=True, use_container_width=True)

            my_pending_list = [res for res in my_reservations if res[4] == "Pending"]
            if my_pending_list:
                with st.form("cancel_my_res_form"):
                    res_to_cancel = st.selectbox(
                        "Cancel a Pending Reservation",
                        my_pending_list,
                        format_func=lambda res: f"Reservation #{res[0]}: {book_names.get(res[1], f'Book #{res[1]}')} (Reserved {res[3]})",
                    )
                    if st.form_submit_button("Cancel Reservation"):
                        with psycopg.connect(DB_URL) as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE reservations SET status = 'Cancelled' WHERE reservation_id = %s;",
                                    (res_to_cancel[0],),
                                )
                        st.rerun()
        else:
            st.info("You have not reserved any books yet.")

    st.stop()

# ==============================================================================
# VIEW B: STAFF ADMIN DASHBOARD
# ==============================================================================
col_title, col_logout = st.columns([6, 1])
with col_title:
    st.title("Bookstore Staff Dashboard")
with col_logout:
    st.write("")
    if st.button("Log Out"):
        st.session_state["role"] = None
        st.rerun()


def render_edit_row_form(table_name, id_col, raw_rows, dict_rows, form_key, expander_label):
    if not raw_rows:
        return
    col_rows = get_editable_columns(table_name, id_col)
    options = list(zip(raw_rows, dict_rows))

    with st.expander(expander_label):
        selected_pair = st.selectbox(
            f"Select Record to Edit ({table_name})",
            options,
            key=f"{form_key}_selector",
            format_func=lambda item: f"{item[0][0]} - {item[0][1]}",
        )
        selected_dict = selected_pair[1]
        record_id = selected_dict[id_col]

        with st.form(f"{form_key}_{record_id}"):
            updated_values = {
                col_name: render_field_input(
                    col_name, data_type, current_val=selected_dict.get(col_name)
                )
                for col_name, data_type in col_rows
            }
            if st.form_submit_button("Save Changes"):
                try:
                    cols = list(updated_values.keys())
                    assignments = sql.SQL(", ").join(
                        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder())
                        for c in cols
                    )
                    query = sql.SQL("UPDATE {} SET {} WHERE {} = {};").format(
                        sql.Identifier(table_name),
                        assignments,
                        sql.Identifier(id_col),
                        sql.Placeholder(),
                    )
                    params = [updated_values[c] for c in cols] + [record_id]
                    with psycopg.connect(DB_URL) as conn:
                        with conn.cursor() as cur:
                            cur.execute(query, params)
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not update record: {e}")


active_rentals = [r for r in all_rentals if r[4] is None]
overdue_rentals = [r for r in active_rentals if r[5] is not None and r[5] < today]
returned_count = len(all_rentals) - len(active_rentals)
total_copies_in_stock = sum(d.get("stock", 0) for d in books_data)
total_active_late_fees = sum(
    calculate_late_fee(r[4], r[5], daily_late_fee) for r in overdue_rentals
)
available_books = [(raw, d) for raw, d in zip(book_rows, books_data) if d.get("stock", 0) > 0]

# --- OVERDUE ALERTS BANNER ---
if overdue_rentals:
    alert_items = [
        f"Rental #{r[0]}: *{book_names.get(r[1], f'Book #{r[1]}')}* borrowed by "
        f"*{customer_names.get(r[2], f'Customer #{r[2]}')}* (Due {r[5]} — {(today - r[5]).days} days overdue | "
        f"Late Fee: *{currency} {calculate_late_fee(r[4], r[5], daily_late_fee):,.2f}*)"
        for r in overdue_rentals
    ]
    st.error(
        f"*Overdue Alert ({len(overdue_rentals)} book(s) past due):*\n\n"
        + "\n".join(f"- {item}" for item in alert_items)
    )

# --- KPI SUMMARY METRICS ---
m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Book Titles", len(book_rows))
m2.metric("Copies in Stock", total_copies_in_stock)
m3.metric("Customers", len(customer_rows))
m4.metric("Active Rentals", len(active_rentals))
m5.metric("Overdue", len(overdue_rentals))
m6.metric("Pending Pickups", len(pending_reservations))
m7.metric("Active Late Fees", f"{currency} {total_active_late_fees:,.2f}")

# --- CUSTOMER PICKUP RESERVATIONS QUEUE ---
if pending_reservations:
    with st.expander(f"Pending Customer Reservations ({len(pending_reservations)} waiting)", expanded=True):
        with st.form("process_reservation_form"):
            selected_res = st.selectbox(
                "Select Reservation",
                pending_reservations,
                format_func=lambda res: (
                    f"Reservation #{res[0]}: {book_names.get(res[1], f'Book #{res[1]}')} — "
                    f"for {customer_names.get(res[2], f'Customer #{res[2]}')} (Requested {res[3]})"
                ),
            )
            res_due_date = st.date_input(
                "Due Date if Approving Checkout",
                value=today + timedelta(days=14),
                min_value=today,
            )
            col_approve, col_decline = st.columns(2)
            with col_approve:
                approve_btn = st.form_submit_button("Approve & Check Out", type="primary")
            with col_decline:
                decline_btn = st.form_submit_button("Cancel Reservation")

            if approve_btn:
                with psycopg.connect(DB_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE books SET stock = stock - 1 WHERE book_id = %s AND stock > 0 RETURNING stock;",
                            (selected_res[1],),
                        )
                        if cur.fetchone() is None:
                            st.error("Cannot fulfill reservation: book is out of stock.")
                        else:
                            cur.execute(
                                "INSERT INTO rentals (book_id, customer_id, borrow_date, due_date) VALUES (%s, %s, %s, %s);",
                                (selected_res[1], selected_res[2], today, res_due_date),
                            )
                            cur.execute(
                                "UPDATE reservations SET status = 'Fulfilled' WHERE reservation_id = %s;",
                                (selected_res[0],),
                            )
                            st.rerun()
            elif decline_btn:
                with psycopg.connect(DB_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE reservations SET status = 'Cancelled' WHERE reservation_id = %s;",
                            (selected_res[0],),
                        )
                st.rerun()

st.divider()

# --- SECTION 1: Check Out (with Due Date) & Return Forms ---
col_checkout, col_return = st.columns(2)

with col_checkout:
    st.subheader("Check Out a Book")
    if available_books and customer_rows:
        with st.form("new_rental_form", clear_on_submit=True):
            selected_book = st.selectbox(
                "Select Available Book",
                available_books,
                format_func=lambda item: f"{item[0][0]} - {item[0][1]} (In stock: {item[1].get('stock', 0)})",
            )
            selected_customer = st.selectbox(
                "Select Customer",
                customer_rows,
                format_func=lambda row: f"{row[0]} - {row[1]}",
            )
            due_date = st.date_input(
                "Due Date",
                value=today + timedelta(days=14),
                min_value=today,
            )
            if st.form_submit_button("Record Rental"):
                book_id = selected_book[1]["book_id"]
                with psycopg.connect(DB_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE books SET stock = stock - 1 WHERE book_id = %s AND stock > 0 RETURNING stock;",
                            (book_id,),
                        )
                        if cur.fetchone() is None:
                            st.error("This book just went out of stock.")
                        else:
                            cur.execute(
                                "INSERT INTO rentals (book_id, customer_id, borrow_date, due_date) VALUES (%s, %s, %s, %s);",
                                (book_id, selected_customer[0], today, due_date),
                            )
                            st.rerun()
    elif not book_rows or not customer_rows:
        st.info("Add a book and customer below to start checking out books.")
    else:
        st.warning("All books are currently out of stock.")

with col_return:
    st.subheader("Return a Book")
    if active_rentals:
        with st.form("return_rental_form", clear_on_submit=True):
            selected_rental = st.selectbox(
                "Select Active Rental",
                active_rentals,
                format_func=lambda r: (
                    f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} — "
                    f"{customer_names.get(r[2], f'Customer #{r[2]}')} | "
                    f"Fee: {currency} {calculate_late_fee(None, r[5], daily_late_fee):,.2f}"
                ),
            )
            if st.form_submit_button("Mark Book Returned"):
                with psycopg.connect(DB_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE rentals SET return_date = %s WHERE rental_id = %s AND return_date IS NULL RETURNING book_id;",
                            (today, selected_rental[0]),
                        )
                        returned_row = cur.fetchone()
                        if returned_row:
                            cur.execute(
                                "UPDATE books SET stock = stock + 1 WHERE book_id = %s;",
                                (returned_row[0],),
                            )
                st.rerun()
    else:
        st.info("No active unreturned rentals right now.")

# --- LATE FEE RATE CONFIGURATION ---
with st.expander(f"Late Fee Settings (Current: {currency} {daily_late_fee:,.2f} / day)"):
    with st.form("late_fee_settings_form"):
        col_curr, col_rate = st.columns(2)
        with col_curr:
            new_currency = st.text_input("Currency Code / Symbol", value=currency).strip() or "KES"
        with col_rate:
            new_rate = st.number_input(
                "Daily Overdue Rate", min_value=0.0, step=5.0, value=daily_late_fee
            )
        if st.form_submit_button("Update Late Fee Settings"):
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE bookstore_settings SET currency = %s, daily_late_fee = %s WHERE id = 1;",
                        (new_currency, new_rate),
                    )
            st.rerun()

# --- SECTION 2: Database Explorer, Analytics & CSV Exports ---
st.subheader("Database Explorer & Reports")
tab_rentals, tab_analytics, tab_books, tab_customers, tab_logs = st.tabs(
    ["Live Rentals (View)", "Analytics", "Books", "Customers", "Audit Logs (Trigger)"]
)

with tab_rentals:
    _, rentals_data = fetch_table("SELECT * FROM rental_details ORDER BY rental_id DESC;")
    rental_meta = {r[0]: {"due_date": r[5], "return_date": r[4]} for r in all_rentals}
    enriched_rentals = []
    for row in rentals_data:
        item = dict(row)
        meta = rental_meta.get(item.get("rental_id"), {})
        due_d = meta.get("due_date")
        ret_d = meta.get("return_date")
        item["due_date"] = due_d
        item["status"] = format_rental_status(ret_d, due_d)
        item[f"late_fee_{currency}"] = calculate_late_fee(ret_d, due_d, daily_late_fee)
        enriched_rentals.append(item)

    search_term = (
        st.text_input(
            "Search Rentals",
            placeholder="Filter by book title, customer name, status, or ID...",
        )
        .strip()
        .lower()
    )
    if search_term:
        filtered_rentals = [
            row
            for row in enriched_rentals
            if any(search_term in str(val).lower() for val in row.values())
        ]
    else:
        filtered_rentals = enriched_rentals

    st.dataframe(filtered_rentals, hide_index=True, use_container_width=True)

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            "Download Rentals CSV",
            data=to_csv_bytes(filtered_rentals),
            file_name=f"rentals_report_{today}.csv",
            mime="text/csv",
            disabled=not filtered_rentals,
        )
    with col_dl2:
        overdue_export = [
            row for row in enriched_rentals if str(row.get("status", "")).startswith("OVERDUE")
        ]
        st.download_button(
            "Download Overdue Report CSV",
            data=to_csv_bytes(overdue_export),
            file_name=f"overdue_rentals_{today}.csv",
            mime="text/csv",
            disabled=not overdue_export,
        )

    if all_rentals:
        with st.expander("Delete a Rental Record"):
            with st.form("delete_rental_form"):
                rental_to_delete = st.selectbox(
                    "Select Rental to Delete",
                    all_rentals,
                    format_func=lambda r: (
                        f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} — "
                        f"{customer_names.get(r[2], f'Customer #{r[2]}')} ({format_rental_status(r[4], r[5])})"
                    ),
                )
                confirm_rental_del = st.checkbox("Confirm permanent deletion of this rental record")
                remove_linked_logs = st.checkbox(
                    "Also remove linked audit log entries if required by database constraints"
                )
                if st.form_submit_button("Delete Rental", type="primary"):
                    if not confirm_rental_del:
                        st.warning("Please tick the confirmation box first.")
                    else:
                        try:
                            with psycopg.connect(DB_URL) as conn:
                                with conn.cursor() as cur:
                                    if remove_linked_logs:
                                        cur.execute(
                                            "DELETE FROM rental_logs WHERE rental_id = %s;",
                                            (rental_to_delete[0],),
                                        )
                                    cur.execute(
                                        "DELETE FROM rentals WHERE rental_id = %s;",
                                        (rental_to_delete[0],),
                                    )
                                    if rental_to_delete[4] is None:
                                        cur.execute(
                                            "UPDATE books SET stock = stock + 1 WHERE book_id = %s;",
                                            (rental_to_delete[1],),
                                        )
                            st.rerun()
                        except psycopg.errors.ForeignKeyViolation:
                            st.error("This rental is referenced in rental_logs. Tick the second box above to remove its linked log entry as well.")
                        except Exception as e:
                            st.error(f"Could not delete rental: {e}")

with tab_analytics:
    if not all_rentals:
        st.info("Record at least one rental to generate bookstore analytics charts.")
    else:
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.markdown("*Most Borrowed Books*")
            book_counts = {}
            for r in all_rentals:
                title = book_names.get(r[1], f"Book #{r[1]}")
                book_counts[title] = book_counts.get(title, 0) + 1
            top_books_chart = [
                {"Book Title": title, "Total Rentals": count}
                for title, count in sorted(book_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ]
            st.bar_chart(top_books_chart, x="Book Title", y="Total Rentals")

        with chart_col2:
            st.markdown("*Top Customers by Rentals*")
            cust_counts = {}
            for r in all_rentals:
                cname = customer_names.get(r[2], f"Customer #{r[2]}")
                cust_counts[cname] = cust_counts.get(cname, 0) + 1
            top_cust_chart = [
                {"Customer": cname, "Total Rentals": count}
                for cname, count in sorted(cust_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ]
            st.bar_chart(top_cust_chart, x="Customer", y="Total Rentals")

        st.markdown("*Checkout Activity Over Time*")
        daily_counts = {}
        for r in all_rentals:
            b_date = str(r[3])
            daily_counts[b_date] = daily_counts.get(b_date, 0) + 1
        trend_chart = [
            {"Borrow Date": d_str, "Checkouts": count}
            for d_str, count in sorted(daily_counts.items())
        ]
        st.line_chart(trend_chart, x="Borrow Date", y="Checkouts")

with tab_books:
    col_b_add, col_b_edit = st.columns(2)
    with col_b_add:
        render_add_row_form("books", "book_id", "add_book_form", "Add New Book")
    with col_b_edit:
        render_edit_row_form(
            "books",
            "book_id",
            book_rows,
            books_data,
            "edit_book_form",
            "Edit Book Details / Stock",
        )

    st.dataframe(books_data, hide_index=True, use_container_width=True)
    st.download_button(
        "Download Inventory CSV",
        data=to_csv_bytes(books_data),
        file_name=f"books_inventory_{today}.csv",
        mime="text/csv",
        disabled=not books_data,
    )
    
    if book_rows:
        with st.expander("Delete a Book"):
            with st.form("delete_book_form"):
                book_to_delete = st.selectbox(
                    "Select Book to Delete",
                    book_rows,
                    format_func=lambda r: f"{r[0]} - {r[1]}",
                )
                confirm_book_del = st.checkbox("Confirm permanent deletion of this book")
                if st.form_submit_button("Delete Book", type="primary"):
                    if not confirm_book_del:
                        st.warning("Please tick the confirmation box first.")
                    else:
                        try:
                            with psycopg.connect(DB_URL) as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "DELETE FROM books WHERE book_id = %s;",
                                        (book_to_delete[0],),
                                    )
                            st.rerun()
                        except psycopg.errors.ForeignKeyViolation:
                            st.error("Cannot delete this book because it still has rental records. Delete its rentals first.")
                        except Exception as e:
                            st.error(f"Could not delete book: {e}")

with tab_customers:
    col_c_add, col_c_edit = st.columns(2)
    with col_c_add:
        render_add_row_form("customers", "customer_id", "add_customer_form", "Add New Customer")
    with col_c_edit:
        render_edit_row_form(
            "customers",
            "customer_id",
            customer_rows,
            customers_data,
            "edit_customer_form",
            "Edit Customer Details",
        )

    st.dataframe(customers_data, hide_index=True, use_container_width=True)
    st.download_button(
        "Download Customers CSV",
        data=to_csv_bytes(customers_data),
        file_name=f"customers_list_{today}.csv",
        mime="text/csv",
        disabled=not customers_data,
    )

    if customer_rows:
        with st.expander("Delete a Customer"):
            with st.form("delete_customer_form"):
                customer_to_delete = st.selectbox(
                    "Select Customer to Delete",
                    customer_rows,
                    format_func=lambda r: f"{r[0]} - {r[1]}",
                )
                confirm_cust_del = st.checkbox("Confirm permanent deletion of this customer")
                if st.form_submit_button("Delete Customer", type="primary"):
                    if not confirm_cust_del:
                        st.warning("Please tick the confirmation box first.")
                    else:
                        try:
                            with psycopg.connect(DB_URL) as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "DELETE FROM customers WHERE customer_id = %s;",
                                        (customer_to_delete[0],),
                                    )
                            st.rerun()
                        except psycopg.errors.ForeignKeyViolation:
                            st.error("Cannot delete this customer because they still have rental records. Delete their rentals first.")
                        except Exception as e:
                            st.error(f"Could not delete customer: {e}")

with tab_logs:
    _, logs_data = fetch_table("SELECT * FROM rental_logs ORDER BY log_id DESC;")
    st.dataframe(logs_data, hide_index=True, use_container_width=True)
    st.download_button(
        "Download Audit Logs CSV",
        data=to_csv_bytes(logs_data),
        file_name=f"rental_audit_logs_{today}.csv",
        mime="text/csv",
        disabled=not logs_data,

    )
