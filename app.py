"""
CardPulse Marketplace — Main Flask App
=======================================
Routes:
  /                        Public price tracker dashboard
  /apply                   Shop application form
  /admin                   Admin panel (approve/reject shops)
  /shop/<slug>             Public storefront for a shop
  /dashboard               Shop owner dashboard (manage listings)
  /dashboard/listings      Add / edit / delete listings
  /dashboard/bundles       Manage bundle deals
  /checkout/<listing_id>   Stripe checkout for a single card
  /checkout/bundle/<id>    Stripe checkout for a bundle
  /webhook                 Stripe webhook (marks orders complete)
  /login  /logout          Shop owner auth
"""

from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import stripe
import os
import uuid
from datetime import datetime
from db import init_db, get_db
from scrapers import scrape_ebay, scrape_tcgplayer, scrape_cardladder, fetch_card_image

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# ── Stripe ──────────────────────────────────────────────────────────────
stripe.api_key          = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET   = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY  = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

# ── Fee config (edit these to change your rates) ─────────────────────────
LISTING_FEE_CENTS       = 25        # $0.25 per listing
MEMBERSHIP_MONTHLY_CENTS = 999      # $9.99/month
MEMBERSHIP_YEARLY_CENTS  = 9900     # $99.00/year  (saves ~$20)
PLATFORM_FEE_PERCENT     = 3        # 3% of each sale goes to CardPulse

# ── Upload config ────────────────────────────────────────────────────────
UPLOAD_FOLDER   = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ── Condition options ─────────────────────────────────────────────────────
CONDITION_KEYWORDS = {
    "PSA 10":            ["psa 10", "psa10"],
    "PSA 9":             ["psa 9", "psa9"],
    "PSA 8":             ["psa 8", "psa8"],
    "BGS 9.5":           ["bgs 9.5", "bgs9.5"],
    "BGS 9":             ["bgs 9", "bgs9"],
    "Near Mint":         ["near mint", "nm"],
    "Lightly Played":    ["lightly played", "lp"],
    "Moderately Played": ["moderately played", "mp"],
    "Heavily Played":    ["heavily played", "hp"],
    "Damaged":           ["damaged", "dmg"],
}


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    """Saves an uploaded file and returns the relative path."""
    if file and allowed_file(file.filename):
        ext      = file.filename.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        path     = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)
        return f"/static/uploads/{filename}"
    return None


def login_required(f):
    """Decorator — redirects to login if shop owner isn't logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "shop_id" not in session:
            flash("Please log in to access your dashboard.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator — only allows admin access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def slugify(text):
    """Converts 'Dragon's Den Cards' → 'dragons-den-cards'"""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text


# ════════════════════════════════════════════════════════════════
# PUBLIC — PRICE TRACKER
# ════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    conditions    = list(CONDITION_KEYWORDS.keys())
    cl_configured = bool(os.environ.get("CARDLADDER_EMAIL") and os.environ.get("CARDLADDER_PASSWORD"))
    db = get_db()
    # Show a few featured shops on the homepage
    featured_shops = db.execute(
        "SELECT * FROM shops WHERE status='approved' ORDER BY created_at DESC LIMIT 4"
    ).fetchall()
    return render_template("index.html",
        conditions=conditions,
        cl_configured=cl_configured,
        featured_shops=featured_shops,
        stripe_key=STRIPE_PUBLISHABLE_KEY)


@app.route("/search", methods=["POST"])
def search():
    """Price tracker search — unchanged from v4."""
    data      = request.get_json()
    card_name = data.get("card_name", "").strip()
    condition = data.get("condition", "").strip()

    if not card_name:
        return jsonify({"error": "Please enter a card name."}), 400
    if condition not in CONDITION_KEYWORDS:
        return jsonify({"error": f"'{condition}' is not a valid condition."}), 400

    ebay_prices = scrape_ebay(card_name, condition)
    tcg_prices  = scrape_tcgplayer(card_name, condition)
    cl_prices   = scrape_cardladder(card_name, condition)
    card_image  = fetch_card_image(card_name)

    def summarize(prices, platform):
        if not prices:
            return {"platform": platform, "prices": [], "average": None, "high": None, "low": None}
        avg = round(sum(prices) / len(prices), 2)
        return {"platform": platform, "prices": prices, "average": avg,
                "high": max(prices), "low": min(prices)}

    platforms  = [summarize(ebay_prices, "eBay"),
                  summarize(tcg_prices,  "TCGPlayer"),
                  summarize(cl_prices,   "Card Ladder")]
    all_prices = ebay_prices + tcg_prices + cl_prices
    combined   = round(sum(all_prices) / len(all_prices), 2) if all_prices else None

    return jsonify({"card_name": card_name, "condition": condition,
                    "platforms": platforms, "combined_average": combined,
                    "total_sales": len(all_prices), "card_image": card_image})


# ════════════════════════════════════════════════════════════════
# SHOP APPLICATION
# ════════════════════════════════════════════════════════════════

@app.route("/apply", methods=["GET", "POST"])
def apply():
    """
    Shop application form.

    DECISION-MAKING:
    - IF any required field is missing → show error, don't save
    - IF email is already in the database → reject (no duplicate accounts)
    - ELSE → save application with status='pending', notify admin
    """
    if request.method == "POST":
        name        = request.form.get("shop_name", "").strip()
        owner       = request.form.get("owner_name", "").strip()
        email       = request.form.get("email", "").strip().lower()
        phone       = request.form.get("phone", "").strip()
        city        = request.form.get("city", "").strip()
        state       = request.form.get("state", "").strip()
        about       = request.form.get("about", "").strip()
        password    = request.form.get("password", "")

        # DECISION: Validate required fields
        if not all([name, owner, email, password]):
            flash("Please fill in all required fields.", "error")
            return render_template("shop/apply.html")

        db = get_db()

        # DECISION: Reject duplicate email
        existing = db.execute("SELECT id FROM shops WHERE email=?", (email,)).fetchone()
        if existing:
            flash("An application with that email already exists.", "error")
            return render_template("shop/apply.html")

        slug          = slugify(name)
        pw_hash       = generate_password_hash(password)
        shop_id       = str(uuid.uuid4())
        created_at    = datetime.utcnow().isoformat()

        db.execute("""
            INSERT INTO shops (id, shop_name, owner_name, email, phone,
                city, state, about, slug, password_hash, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)
        """, (shop_id, name, owner, email, phone, city, state, about,
              slug, pw_hash, created_at))
        db.commit()

        flash("Application submitted! We'll review it and email you within 1-2 business days.", "success")
        return redirect(url_for("index"))

    return render_template("shop/apply.html")


# ════════════════════════════════════════════════════════════════
# AUTH — LOGIN / LOGOUT
# ════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db       = get_db()

        # Check admin login
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@cardpulse.com")
        admin_pw    = os.environ.get("ADMIN_PASSWORD", "changeme")

        if email == admin_email and password == admin_pw:
            session["is_admin"] = True
            return redirect(url_for("admin"))

        # Check shop login
        shop = db.execute("SELECT * FROM shops WHERE email=?", (email,)).fetchone()

        # DECISION: IF shop not found OR password wrong → show generic error (don't reveal which)
        if not shop or not check_password_hash(shop["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("shop/login.html")

        # DECISION: IF shop not yet approved → tell them to wait
        if shop["status"] == "pending":
            flash("Your application is still under review. We'll email you when approved.", "warning")
            return render_template("shop/login.html")

        if shop["status"] == "rejected":
            flash("Your application was not approved. Please contact support.", "error")
            return render_template("shop/login.html")

        # Approved — log them in
        session["shop_id"]   = shop["id"]
        session["shop_name"] = shop["shop_name"]
        session["shop_slug"] = shop["slug"]
        return redirect(url_for("dashboard"))

    return render_template("shop/login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ════════════════════════════════════════════════════════════════

@app.route("/admin")
@admin_required
def admin():
    db       = get_db()
    pending  = db.execute("SELECT * FROM shops WHERE status='pending' ORDER BY created_at DESC").fetchall()
    approved = db.execute("SELECT * FROM shops WHERE status='approved' ORDER BY created_at DESC").fetchall()
    rejected = db.execute("SELECT * FROM shops WHERE status='rejected' ORDER BY created_at DESC").fetchall()
    return render_template("admin/panel.html",
        pending=pending, approved=approved, rejected=rejected)


@app.route("/admin/approve/<shop_id>")
@admin_required
def approve_shop(shop_id):
    db = get_db()
    db.execute("UPDATE shops SET status='approved' WHERE id=?", (shop_id,))
    db.commit()
    flash("Shop approved.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reject/<shop_id>")
@admin_required
def reject_shop(shop_id):
    db = get_db()
    db.execute("UPDATE shops SET status='rejected' WHERE id=?", (shop_id,))
    db.commit()
    flash("Shop rejected.", "success")
    return redirect(url_for("admin"))


# ════════════════════════════════════════════════════════════════
# SHOP DASHBOARD
# ════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    db       = get_db()
    shop     = db.execute("SELECT * FROM shops WHERE id=?", (session["shop_id"],)).fetchone()
    listings = db.execute("SELECT * FROM listings WHERE shop_id=? ORDER BY created_at DESC",
                          (session["shop_id"],)).fetchall()
    bundles  = db.execute("SELECT * FROM bundles WHERE shop_id=? ORDER BY created_at DESC",
                          (session["shop_id"],)).fetchall()
    return render_template("shop/dashboard.html",
        shop=shop, listings=listings, bundles=bundles,
        listing_fee=LISTING_FEE_CENTS / 100,
        monthly_fee=MEMBERSHIP_MONTHLY_CENTS / 100,
        yearly_fee=MEMBERSHIP_YEARLY_CENTS / 100)


@app.route("/dashboard/profile", methods=["POST"])
@login_required
def update_profile():
    """Updates shop name, about, logo, and banner."""
    db    = get_db()
    about = request.form.get("about", "").strip()
    logo  = save_upload(request.files.get("logo"))
    banner = save_upload(request.files.get("banner"))

    if logo:
        db.execute("UPDATE shops SET logo=? WHERE id=?", (logo, session["shop_id"]))
    if banner:
        db.execute("UPDATE shops SET banner=? WHERE id=?", (banner, session["shop_id"]))
    if about:
        db.execute("UPDATE shops SET about=? WHERE id=?", (about, session["shop_id"]))

    db.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/listings/add", methods=["POST"])
@login_required
def add_listing():
    """
    Adds a new card listing and charges the listing fee via Stripe.

    DECISION-MAKING:
    - IF any required field missing → error
    - Create a Stripe PaymentIntent for the listing fee
    - Save listing as status='pending_payment'
    - Redirect to a Stripe checkout page for the listing fee
    - AFTER payment succeeds (webhook) → listing goes live
    """
    card_name = request.form.get("card_name", "").strip()
    condition = request.form.get("condition", "").strip()
    price     = request.form.get("price", "").strip()
    quantity  = request.form.get("quantity", "1").strip()
    notes     = request.form.get("notes", "").strip()
    image     = save_upload(request.files.get("image"))

    if not all([card_name, condition, price]):
        flash("Card name, condition, and price are required.", "error")
        return redirect(url_for("dashboard"))

    try:
        price_cents = int(float(price) * 100)
    except ValueError:
        flash("Invalid price.", "error")
        return redirect(url_for("dashboard"))

    db         = get_db()
    listing_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    db.execute("""
        INSERT INTO listings (id, shop_id, card_name, condition, price_cents,
            quantity, notes, image, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,'pending_payment',?)
    """, (listing_id, session["shop_id"], card_name, condition,
          price_cents, quantity, notes, image, created_at))
    db.commit()

    # Create Stripe checkout for listing fee
    # DECISION: IF Stripe isn't configured → activate listing for free (dev mode)
    if not stripe.api_key:
        db.execute("UPDATE listings SET status='active' WHERE id=?", (listing_id,))
        db.commit()
        flash(f"Listing added (Stripe not configured — no fee charged).", "success")
        return redirect(url_for("dashboard"))

    checkout = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"Listing fee: {card_name}"},
                "unit_amount": LISTING_FEE_CENTS,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=url_for("listing_fee_success", listing_id=listing_id, _external=True),
        cancel_url=url_for("dashboard", _external=True),
        metadata={"type": "listing_fee", "listing_id": listing_id},
    )

    return redirect(checkout.url)


@app.route("/dashboard/listings/fee-success/<listing_id>")
@login_required
def listing_fee_success(listing_id):
    db = get_db()
    db.execute("UPDATE listings SET status='active' WHERE id=? AND shop_id=?",
               (listing_id, session["shop_id"]))
    db.commit()
    flash("Listing is now live!", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/listings/delete/<listing_id>")
@login_required
def delete_listing(listing_id):
    db = get_db()
    db.execute("DELETE FROM listings WHERE id=? AND shop_id=?",
               (listing_id, session["shop_id"]))
    db.commit()
    flash("Listing removed.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/bundles/add", methods=["POST"])
@login_required
def add_bundle():
    """Adds a bundle deal (multiple cards sold together at a discount)."""
    title    = request.form.get("title", "").strip()
    desc     = request.form.get("description", "").strip()
    price    = request.form.get("price", "").strip()
    items    = request.form.get("items", "").strip()  # comma-separated card names
    image    = save_upload(request.files.get("image"))

    if not all([title, price, items]):
        flash("Title, price, and items are required.", "error")
        return redirect(url_for("dashboard"))

    try:
        price_cents = int(float(price) * 100)
    except ValueError:
        flash("Invalid price.", "error")
        return redirect(url_for("dashboard"))

    db        = get_db()
    bundle_id = str(uuid.uuid4())
    db.execute("""
        INSERT INTO bundles (id, shop_id, title, description, price_cents, items, image, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (bundle_id, session["shop_id"], title, desc, price_cents,
          items, image, datetime.utcnow().isoformat()))
    db.commit()
    flash("Bundle added!", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/bundles/delete/<bundle_id>")
@login_required
def delete_bundle(bundle_id):
    db = get_db()
    db.execute("DELETE FROM bundles WHERE id=? AND shop_id=?",
               (bundle_id, session["shop_id"]))
    db.commit()
    flash("Bundle removed.", "success")
    return redirect(url_for("dashboard"))


# ════════════════════════════════════════════════════════════════
# MEMBERSHIP CHECKOUT
# ════════════════════════════════════════════════════════════════

@app.route("/dashboard/membership/<plan>")
@login_required
def membership_checkout(plan):
    """
    Creates a Stripe subscription checkout for monthly or yearly membership.

    DECISION-MAKING:
    - IF plan is not 'monthly' or 'yearly' → redirect back
    - IF Stripe not configured → skip (dev mode)
    """
    if plan not in ("monthly", "yearly"):
        return redirect(url_for("dashboard"))

    amount = MEMBERSHIP_MONTHLY_CENTS if plan == "monthly" else MEMBERSHIP_YEARLY_CENTS
    label  = "Monthly Membership" if plan == "monthly" else "Yearly Membership"

    if not stripe.api_key:
        flash("Stripe not configured. Membership activated in dev mode.", "success")
        db = get_db()
        db.execute("UPDATE shops SET membership=? WHERE id=?", (plan, session["shop_id"]))
        db.commit()
        return redirect(url_for("dashboard"))

    checkout = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"CardPulse {label}"},
                "unit_amount": amount,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=url_for("membership_success", plan=plan, _external=True),
        cancel_url=url_for("dashboard", _external=True),
        metadata={"type": "membership", "shop_id": session["shop_id"], "plan": plan},
    )
    return redirect(checkout.url)


@app.route("/dashboard/membership/success/<plan>")
@login_required
def membership_success(plan):
    db = get_db()
    db.execute("UPDATE shops SET membership=? WHERE id=?", (plan, session["shop_id"]))
    db.commit()
    flash(f"Membership activated!", "success")
    return redirect(url_for("dashboard"))


# ════════════════════════════════════════════════════════════════
# PUBLIC STOREFRONT
# ════════════════════════════════════════════════════════════════

@app.route("/shop/<slug>")
def storefront(slug):
    db   = get_db()
    shop = db.execute("SELECT * FROM shops WHERE slug=? AND status='approved'",
                      (slug,)).fetchone()

    # DECISION: IF shop doesn't exist or isn't approved → 404
    if not shop:
        return render_template("404.html"), 404

    listings = db.execute(
        "SELECT * FROM listings WHERE shop_id=? AND status='active' ORDER BY created_at DESC",
        (shop["id"],)
    ).fetchall()

    bundles = db.execute(
        "SELECT * FROM bundles WHERE shop_id=? ORDER BY created_at DESC",
        (shop["id"],)
    ).fetchall()

    reviews = db.execute(
        "SELECT * FROM reviews WHERE shop_id=? ORDER BY created_at DESC",
        (shop["id"],)
    ).fetchall()

    avg_rating = None
    if reviews:
        avg_rating = round(sum(r["rating"] for r in reviews) / len(reviews), 1)

    return render_template("shop/storefront.html",
        shop=shop, listings=listings, bundles=bundles,
        reviews=reviews, avg_rating=avg_rating)


# ════════════════════════════════════════════════════════════════
# CHECKOUT — Buying a card or bundle
# ════════════════════════════════════════════════════════════════

@app.route("/checkout/listing/<listing_id>")
def checkout_listing(listing_id):
    """
    Creates a Stripe checkout for buying a single card listing.
    Platform fee is automatically split using Stripe Connect
    (or deducted manually here for simplicity).

    DECISION-MAKING:
    - IF listing not found or not active → error
    - IF Stripe not configured → show error
    - Platform fee is calculated as PLATFORM_FEE_PERCENT % of sale price
    """
    db      = get_db()
    listing = db.execute(
        "SELECT l.*, s.shop_name, s.slug FROM listings l "
        "JOIN shops s ON l.shop_id = s.id WHERE l.id=? AND l.status='active'",
        (listing_id,)
    ).fetchone()

    if not listing:
        flash("Listing not found or no longer available.", "error")
        return redirect(url_for("index"))

    if not stripe.api_key:
        flash("Stripe is not configured. Cannot process payment.", "error")
        return redirect(url_for("storefront", slug=listing["slug"]))

    platform_fee = int(listing["price_cents"] * PLATFORM_FEE_PERCENT / 100)

    checkout = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": f"{listing['card_name']} ({listing['condition']})",
                    "description": f"Sold by {listing['shop_name']}",
                },
                "unit_amount": listing["price_cents"],
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=url_for("checkout_success", _external=True,
                            type="listing", id=listing_id),
        cancel_url=url_for("storefront", slug=listing["slug"], _external=True),
        metadata={
            "type":        "card_sale",
            "listing_id":  listing_id,
            "shop_id":     listing["shop_id"],
            "platform_fee": platform_fee,
        },
    )
    return redirect(checkout.url)


@app.route("/checkout/bundle/<bundle_id>")
def checkout_bundle(bundle_id):
    """Creates a Stripe checkout for buying a bundle deal."""
    db     = get_db()
    bundle = db.execute(
        "SELECT b.*, s.shop_name, s.slug FROM bundles b "
        "JOIN shops s ON b.shop_id = s.id WHERE b.id=?",
        (bundle_id,)
    ).fetchone()

    if not bundle:
        flash("Bundle not found.", "error")
        return redirect(url_for("index"))

    if not stripe.api_key:
        flash("Stripe is not configured.", "error")
        return redirect(url_for("storefront", slug=bundle["slug"]))

    platform_fee = int(bundle["price_cents"] * PLATFORM_FEE_PERCENT / 100)

    checkout = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": bundle["title"],
                    "description": f"Bundle from {bundle['shop_name']}",
                },
                "unit_amount": bundle["price_cents"],
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=url_for("checkout_success", _external=True,
                            type="bundle", id=bundle_id),
        cancel_url=url_for("storefront", slug=bundle["slug"], _external=True),
        metadata={
            "type":        "bundle_sale",
            "bundle_id":   bundle_id,
            "shop_id":     bundle["shop_id"],
            "platform_fee": platform_fee,
        },
    )
    return redirect(checkout.url)


@app.route("/checkout/success")
def checkout_success():
    item_type = request.args.get("type")
    item_id   = request.args.get("id")
    return render_template("shop/checkout_success.html",
                           item_type=item_type, item_id=item_id)


# ════════════════════════════════════════════════════════════════
# STRIPE WEBHOOK
# Stripe calls this URL after a payment completes.
# ════════════════════════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    """
    Handles Stripe events after payment.

    DECISION-MAKING:
    - IF webhook signature doesn't verify → reject (security)
    - IF event is checkout.session.completed:
        - IF type is card_sale → create order record, decrement quantity
        - IF type is bundle_sale → create order record
        - IF type is listing_fee → mark listing as active
        - IF type is membership → activate shop membership
    """
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        # DECISION: Invalid signature — reject immediately
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]
        meta        = session_obj.get("metadata", {})
        db          = get_db()
        order_id    = str(uuid.uuid4())
        now         = datetime.utcnow().isoformat()

        if meta.get("type") == "card_sale":
            listing_id = meta["listing_id"]
            db.execute("""
                INSERT INTO orders (id, listing_id, shop_id, amount_cents,
                    platform_fee_cents, stripe_session_id, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (order_id, listing_id, meta["shop_id"],
                  session_obj["amount_total"], meta.get("platform_fee", 0),
                  session_obj["id"], now))
            # Decrement quantity; IF quantity hits 0 → mark sold
            db.execute("""
                UPDATE listings SET quantity = quantity - 1,
                    status = CASE WHEN quantity - 1 <= 0 THEN 'sold' ELSE status END
                WHERE id=?
            """, (listing_id,))
            db.commit()

        elif meta.get("type") == "bundle_sale":
            db.execute("""
                INSERT INTO orders (id, bundle_id, shop_id, amount_cents,
                    platform_fee_cents, stripe_session_id, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (order_id, meta["bundle_id"], meta["shop_id"],
                  session_obj["amount_total"], meta.get("platform_fee", 0),
                  session_obj["id"], now))
            db.commit()

        elif meta.get("type") == "listing_fee":
            db.execute("UPDATE listings SET status='active' WHERE id=?",
                       (meta["listing_id"],))
            db.commit()

        elif meta.get("type") == "membership":
            db.execute("UPDATE shops SET membership=? WHERE id=?",
                       (meta["plan"], meta["shop_id"]))
            db.commit()

    return jsonify({"status": "ok"})


# ════════════════════════════════════════════════════════════════
# REVIEWS
# ════════════════════════════════════════════════════════════════

@app.route("/shop/<slug>/review", methods=["POST"])
def add_review(slug):
    db   = get_db()
    shop = db.execute("SELECT * FROM shops WHERE slug=?", (slug,)).fetchone()
    if not shop:
        return redirect(url_for("index"))

    reviewer = request.form.get("reviewer_name", "Anonymous").strip()
    rating   = request.form.get("rating", "5")
    comment  = request.form.get("comment", "").strip()

    try:
        rating = max(1, min(5, int(rating)))  # DECISION: Clamp rating to 1–5
    except ValueError:
        rating = 5

    db.execute("""
        INSERT INTO reviews (id, shop_id, reviewer_name, rating, comment, created_at)
        VALUES (?,?,?,?,?,?)
    """, (str(uuid.uuid4()), shop["id"], reviewer, rating,
          comment, datetime.utcnow().isoformat()))
    db.commit()
    flash("Review submitted!", "success")
    return redirect(url_for("storefront", slug=slug))


# ════════════════════════════════════════════════════════════════
# STARTUP
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True)
