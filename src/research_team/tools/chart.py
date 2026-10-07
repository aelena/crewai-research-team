"""Chart tool for the report writer: one data series in, a PNG and its markdown image link out."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, model_validator


class ChartInput(BaseModel):
    title: str = Field(description="Chart title, specific (what, where, when)")
    chart_type: Literal["bar", "barh", "line", "pie"] = "bar"
    labels: list[str] = Field(description="Category or x-axis labels")
    values: list[float] = Field(description="One numeric value per label")
    unit: str = Field("", description="Unit of the values, e.g. '%', 'USD bn'")
    source: str = Field(description="Source caption, publisher and year, shown under the chart")

    @model_validator(mode="after")
    def _same_length(self) -> ChartInput:
        if len(self.labels) != len(self.values) or not self.labels:
            raise ValueError("labels and values must be non-empty and the same length")
        return self


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "chart"


class ChartGeneratorTool(BaseTool):
    name: str = "chart_generator"
    description: str = (
        "Render one verified data series as a PNG chart. Only chart numbers that appear in the verified "
        "claim ledgers, and always give their source. Returns the markdown image link to paste into the report."
    )
    args_schema: type[BaseModel] = ChartInput
    out_dir: Path
    link_prefix: str = "charts"

    def _run(self, title: str, labels: list[str], values: list[float], source: str,
             chart_type: str = "bar", unit: str = "") -> str:
        from matplotlib.figure import Figure  # the object API, no pyplot: safe off the main thread

        fig = Figure(figsize=(8, 4.5), dpi=150)
        ax = fig.add_subplot()
        if chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90)
            ax.axis("equal")
        elif chart_type == "line":
            ax.plot(labels, values, marker="o")
        elif chart_type == "barh":
            ax.barh(labels, values)
        else:
            ax.bar(labels, values)
        if chart_type != "pie":
            ax.set_ylabel(unit) if chart_type != "barh" else ax.set_xlabel(unit)
            ax.spines[["top", "right"]].set_visible(False)
            ax.tick_params(axis="x", labelrotation=0 if len(labels) < 6 else 30)
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
        fig.text(0.01, 0.01, f"Source: {source}", fontsize=7, color="#555")
        fig.tight_layout(rect=(0, 0.04, 1, 1))

        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{_slug(title)}.png"
        fig.savefig(self.out_dir / name)
        return f"![{title}]({self.link_prefix}/{name})"
