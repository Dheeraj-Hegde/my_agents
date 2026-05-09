import json
import os
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Card, Grid, Column, Row, Text, Heading, 
    Metric, DataTable, DataTableColumn, Button, Input,
    Badge, Separator, H1, H2, H3, H4, Div, Container, Muted,
    Tabs, Tab, Icon, Page, Pages, Lead, Large
)
from prefab_ui.components.charts import BarChart, ChartSeries, AreaChart, PieChart, Sparkline
from prefab_ui.themes import Presentation
from datetime import datetime

# Load data helper
def load_json(filename, default=[]):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except:
            return default
    return default

def get_stats():
    stats = {"from": {}, "to": {}}
    if os.path.exists("from_currencies.txt"):
        with open("from_currencies.txt", "r") as f:
            for line in f:
                curr = line.strip()
                stats["from"][curr] = stats["from"].get(curr, 0) + 1
    if os.path.exists("to_currencies.txt"):
        with open("to_currencies.txt", "r") as f:
            for line in f:
                curr = line.strip()
                stats["to"][curr] = stats["to"].get(curr, 0) + 1
    return stats

# Build the Expanded Intelligence Suite
with PrefabApp(
    title="CurrAI | Executive intelligence Suite",
    theme=Presentation(),
    css_class="max-w-none" # Use full screen width
) as app:
    
    # --- HEADER ---
    with Div(padding=8, border_bottom=True):
        with Row(align="center", justify="between"):
            with Row(align="center", gap=4):
                Icon(name="shield", size="lg", color="#818cf8")
                with Div():
                    H1("CurrAI EXECUTIVE")
                    Muted("Strategic Forex Intelligence Hub")
            Badge("SECURE DATA STREAM", variant="default")

    # --- MAIN CONTENT ---
    with Container(padding=8):
        
        # 1. Performance Overview (Full Width Metrics)
        stats = get_stats()
        with Grid(cols=3, gap=8, margin_bottom=10):
            with Card():
                with Div(padding=8):
                    Metric(label="Total Logged Volume", value=str(sum(stats["from"].values())), description="Global audit trail")
                    Sparkline(data=[12, 18, 14, 22, 20, 28, 30, 35], height=50, color="#818cf8", fill=True)
            with Card():
                with Div(padding=8):
                    Metric(label="Market Breadth", value=str(len(stats["from"])), description="Active currency clusters")
                    Sparkline(data=[4, 5, 5, 6, 8, 9, 10, 10], height=50, variant="success", fill=True)
            with Card():
                with Div(padding=8):
                    news_data = load_json("currency_news.json", {})
                    news_count = sum(len(v) for v in news_data.values())
                    Metric(label="Intelligence Feed", value=str(news_count), description="AI-captured signals")
                    Sparkline(data=[15, 25, 20, 35, 30, 45, 40, 50], height=50, variant="info", fill=True)

        # 2. Main Workspace (Tabbed)
        with Tabs(value="analytics"):
            
            # --- TAB 1: ANALYTICS ---
            with Tab("Strategic Analytics", value="analytics"):
                with Grid(cols=2, gap=10, margin_top=8):
                    with Card():
                        with Div(padding=10):
                            H2("Regional Request Distribution", margin_bottom=8)
                            if stats["from"]:
                                dist_data = [{"name": k, "value": v} for k, v in stats["from"].items()]
                                PieChart(
                                    data=dist_data,
                                    data_key="value",
                                    name_key="name",
                                    inner_radius=60,
                                    height=550
                                )
                                with Div(margin_top=4, text_align="center"):
                                    Muted(f"Active Nodes: {len(dist_data)} Currencies")
                            else:
                                with Div(padding=20, text_align="center"):
                                    Muted("No source data detected in from_currencies.txt")
                    with Card():
                        with Div(padding=10):
                            H2("Target Asset Volume", margin_bottom=8)
                            if stats["to"]:
                                to_data = [{"curr": k, "count": v} for k, v in stats["to"].items()]
                                BarChart(
                                    data=to_data,
                                    series=[ChartSeries(data_key="count", label="Volume", color="#f472b6")],
                                    x_axis="curr",
                                    height=550, # Increased height
                                    bar_radius=10
                                )

            # --- TAB 2: NEWS FEED ---
            with Tab("Intelligence Signals", value="news"):
                with Div(margin_top=8):
                    if news_data:
                        with Grid(cols=2, gap=8):
                            for currency, articles in news_data.items():
                                with Card():
                                    with Div(padding=8):
                                        with Row(justify="between", align="center"):
                                            H3(f"Sourced: {currency}")
                                            Badge("Live Signal", variant="default")
                                        Separator(margin_y=6)
                                        for art in articles:
                                            with Div(margin_top=4, padding_left=6, border_left=True):
                                                Text(art['text'], weight="semibold", size="lg")
                                                Muted(art['time'], size="sm")
                    else:
                        with Card():
                            with Div(padding=20, text_align="center"):
                                Muted("No active intelligence feeds. Execute a trade to begin capture.", size="lg")

            # --- TAB 3: EXECUTION LOGS ---
            with Tab("Audit Archive", value="logs"):
                with Card(margin_top=8):
                    with Div(padding=10):
                        H2("Master Execution Audit", margin_bottom=8)
                        history = load_json("forex_history.json", [])
                        if history:
                            table_data = [
                                {"Pair": h.get('pair', '??'), "Rate": str(h.get('price', '0')), "Time": h.get('time', '..')} 
                                for h in reversed(history)
                            ]
                            DataTable(
                                columns=[
                                    DataTableColumn(key="Time", header="Timestamp", sortable=True, width="300px"),
                                    DataTableColumn(key="Pair", header="Asset Pair", width="200px"),
                                    DataTableColumn(key="Rate", header="Price", width="200px"),
                                ],
                                rows=table_data,
                                paginated=True,
                                search=True,
                                page_size=15 # More rows visible
                            )
                        else:
                            Muted("Archive is currently offline.")

print("Expanded Command Center Dashboard generated.")
