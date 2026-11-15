from datetime import date, timedelta
import hmac
import psycopg
from psycopg import sql
import streamlit as st

st.set_page_config(page_title="Bookstore Cloud Dashboard", layout="wide")

DB_URL = st.secrets["DB_URL"]


def fetch_table(query, params=None):
  with psycopg.connect(DB_URL) as conn:
    with conn.cursor() as cur:
      cur.execute(query, params or ())
      rows = cur.fetchall()
      columns = [desc[0] for desc in cur.description]
  return rows, [dict(zip(columns, row)) for row in rows]


# Ensure return_date, due_date, and book stock columns exist
with psycopg.connect(DB_URL) as conn:
  with conn.cursor() as cur:
    cur.execute("ALTER TABLE rentals ADD COLUMN IF NOT EXISTS return_date DATE;")
    cur.execute("ALTER TABLE rentals ADD COLUMN IF NOT EXISTS due_date DATE;")
    cur.execute(
        "ALTER TABLE books ADD COLUMN IF NOT EXISTS stock INTEGER NOT NULL"
        " DEFAULT 1;"
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
    default_int = (
        int(current_val)
        if current_val is not None
        else (1 if col_name == "stock" else 0)
    )
    return st.number_input(
        label,
        step=1,
        min_value=0 if col_name == "stock" else None,
        value=default_int,
    )
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
      entered_cust_pass = st.text_input(
          "Customer Portal Password", type="password"
      )
      if st.form_submit_button("Log In as Customer"):
        if not cust_identifier:
          st.warning("Please enter your name or Customer ID.")
        elif not hmac.compare_digest(entered_cust_pass, customer_pass):
          st.error("Incorrect customer portal password.")
        else:
          cust_rows, _ = fetch_table(
              "SELECT * FROM customers ORDER BY customer_id;"
          )
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
            st.error(
                "Customer not found. Check your spelling or register below."
            )

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


# Load shared data
book_rows, books_data = fetch_table("SELECT * FROM books ORDER BY book_id;")
customer_rows, customers_data = fetch_table(
    "SELECT * FROM customers ORDER BY customer_id;"
)
all_rentals, _ = fetch_table("""
    SELECT rental_id, book_id, customer_id, borrow_date, return_date, due_date
    FROM rentals
    ORDER BY rental_id DESC;
    """)

book_names = {r[0]: r[1] for r in book_rows}
customer_names = {r[0]: r[1] for r in customer_rows}
today = date.today()

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
  my_overdue = [
      r for r in my_active if r[5] is not None and r[5] < today
  ]

  if my_overdue:
    overdue_titles = ", ".join(
        f"{book_names.get(r[1], f'Book #{r[1]}')} (due {r[5]})"
        for r in my_overdue
    )
    st.error(f"Overdue Notice: Please return {overdue_titles}.")

  c1, c2, c3 = st.columns(3)
  c1.metric("My Active Rentals", len(my_active))
  c2.metric("My Overdue Books", len(my_overdue))
  c3.metric("Total Books Borrowed", len(my_rentals))

  st.divider()
  tab_my_rentals, tab_catalog = st.tabs(
      ["My Borrowed Books & Due Dates", "Browse Available Books"]
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
          }
          for r in my_rentals
      ]
      st.dataframe(my_table, hide_index=True, use_container_width=True)
    else:
      st.info("You have no rental history yet.")

  with tab_catalog:
    catalog_search = (
        st.text_input(
            "Search Book Catalog", placeholder="Search by title or details..."
        )
        .strip()
        .lower()
    )
    catalog_rows = []
    for row_dict in books_data:
      clean_row = dict(row_dict)
      copies = clean_row.get("stock", 0)
      clean_row["Availability"] = (
          f"Available ({copies} in stock)" if copies > 0 else "Out of Stock"
      )
      if not catalog_search or any(
          catalog_search in str(v).lower() for v in clean_row.values()
      ):
        catalog_rows.append(clean_row)
    st.dataframe(catalog_rows, hide_index=True, use_container_width=True)

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


def render_edit_row_form(
    table_name, id_col, raw_rows, dict_rows, form_key, expander_label
):
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
overdue_rentals = [
    r for r in active_rentals if r[5] is not None and r[5] < today
]
returned_count = len(all_rentals) - len(active_rentals)
total_copies_in_stock = sum(d.get("stock", 0) for d in books_data)
available_books = [
    (raw, d) for raw, d in zip(book_rows, books_data) if d.get("stock", 0) > 0
]

# --- OVERDUE ALERTS BANNER ---
if overdue_rentals:
  alert_items = [
      f"Rental #{r[0]}: *{book_names.get(r[1], f'Book #{r[1]}')}* borrowed by"
      f" *{customer_names.get(r[2], f'Customer #{r[2]}')}* (Due {r[5]} —"
      f" {(today - r[5]).days} days overdue)"
      for r in overdue_rentals
  ]
  st.error(
      f"*Overdue Alert ({len(overdue_rentals)} book(s) past due):*\n\n"
      + "\n".join(f"- {item}" for item in alert_items)
  )

# --- KPI SUMMARY METRICS ---
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Book Titles", len(book_rows))
m2.metric("Copies in Stock", total_copies_in_stock)
m3.metric("Total Customers", len(customer_rows))
m4.metric("Active Rentals", len(active_rentals))
m5.metric("Overdue Rentals", len(overdue_rentals))
m6.metric("Returned Rentals", returned_count)

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
          format_func=lambda item: (
              f"{item[0][0]} - {item[0][1]} (In stock: {item[1].get('stock', 0)})"
          ),
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
                "UPDATE books SET stock = stock - 1 WHERE book_id = %s AND"
                " stock > 0 RETURNING stock;",
                (book_id,),
            )
            if cur.fetchone() is None:
              st.error("This book just went out of stock.")
            else:
              cur.execute(
                  "INSERT INTO rentals (book_id, customer_id, borrow_date,"
                  " due_date) VALUES (%s, %s, %s, %s);",
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
              f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} —"
              f" {customer_names.get(r[2], f'Customer #{r[2]}')} | Due:"
              f" {r[5] if r[5] else 'N/A'}"
          ),
      )
      if st.form_submit_button("Mark Book Returned"):
        with psycopg.connect(DB_URL) as conn:
          with conn.cursor() as cur:
            cur.execute(
                "UPDATE rentals SET return_date = %s WHERE rental_id = %s AND"
                " return_date IS NULL RETURNING book_id;",
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

# --- SECTION 2: Database Explorer, Search, Edit & Delete Controls ---
st.subheader("Database Explorer")
tab_rentals, tab_books, tab_customers, tab_logs = st.tabs(
    ["Live Rentals (View)", "Books", "Customers", "Audit Logs (Trigger)"]
)

with tab_rentals:
  _, rentals_data = fetch_table(
      "SELECT * FROM rental_details ORDER BY rental_id DESC;"
  )
  rental_meta = {
      r[0]: {"due_date": r[5], "return_date": r[4]} for r in all_rentals
  }
  enriched_rentals = []
  for row in rentals_data:
    item = dict(row)
    meta = rental_meta.get(item.get("rental_id"), {})
    item["due_date"] = meta.get("due_date")
    item["status"] = format_rental_status(
        meta.get("return_date"), meta.get("due_date")
    )
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

  if all_rentals:
    with st.expander("Delete a Rental Record"):
      with st.form("delete_rental_form"):
        rental_to_delete = st.selectbox(
            "Select Rental to Delete",
            all_rentals,
            format_func=lambda r: (
                f"Rental #{r[0]}: {book_names.get(r[1], f'Book #{r[1]}')} —"
                f" {customer_names.get(r[2], f'Customer #{r[2]}')} ({format_rental_status(r[4], r[5])})"
            ),
        )
        confirm_rental_del = st.checkbox(
            "Confirm permanent deletion of this rental record"
        )
        remove_linked_logs = st.checkbox(
            "Also remove linked audit log entries if required by database"
            " constraints"
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
              st.error(
                  "This rental is referenced in rental_logs. Tick the second"
                  " box above to remove its linked log entry as well."
              )
            except Exception as e:
              st.error(f"Could not delete rental: {e}")

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

  if book_rows:
    with st.expander("Delete a Book"):
      with st.form("delete_book_form"):
        book_to_delete = st.selectbox(
            "Select Book to Delete",
            book_rows,
            format_func=lambda r: f"{r[0]} - {r[1]}",
        )
        confirm_book_del = st.checkbox(
            "Confirm permanent deletion of this book"
        )
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
              except psycopg.errors.ForeignKeyViolation:st.error(
                  "This rental is referenced in rental_logs. Tick the second"
                  " box above to remove its linked log entry as well."
              )
            except Exception as e:
              st.error(f"Could not delete rental: {e}")

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

  if book_rows:
    with st.expander("Delete a Book"):
      with st.form("delete_book_form"):
        book_to_delete = st.selectbox(
            "Select Book to Delete",
            book_rows,
            format_func=lambda r: f"{r[0]} - {r[1]}",
        )
        confirm_book_del = st.checkbox(
            "Confirm permanent deletion of this book"
        )
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
              st.error(
                  "Cannot delete this book because it still has rental records."
                  " Delete its rentals first."
              )
            except Exception as e:
              st.error(f"Could not delete book: {e}")

with tab_customers:
  col_c_add, col_c_edit = st.columns(2)
  with col_c_add:
    render_add_row_form(
        "customers", "customer_id", "add_customer_form", "Add New Customer"
    )
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

  if customer_rows:
    with st.expander("Delete a Customer"):
      with st.form("delete_customer_form"):
        customer_to_delete = st.selectbox(
            "Select Customer to Delete",
            customer_rows,
            format_func=lambda r: f"{r[0]} - {r[1]}",
        )
        confirm_cust_del = st.checkbox(
            "Confirm permanent deletion of this customer"
        )
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
              st.error(
                  "Cannot delete this customer because they still have rental"
                  " records. Delete their rentals first."
              )
            except Exception as e:
              st.error(f"Could not delete customer: {e}")

with tab_logs:
  _, logs_data = fetch_table("SELECT * FROM rental_logs ORDER BY log_id DESC;")
  st.dataframe(logs_data, hide_index=True, use_container_width=True)
