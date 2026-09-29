"""Optional stateless MCP example: unit conversion only, no business or host access."""

from decimal import Decimal
from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

server = MCPServer("SemiBrain measurement utilities", log_level="WARNING")


@server.tool()
def convert_length(value: Annotated[float, Field(ge=0, le=1e18)],
                   from_unit: Literal["nm", "um", "mm"],
                   to_unit: Literal["nm", "um", "mm"]) -> str:
    """Convert a supplied nonnegative length between nm, um (micrometre), and mm.

    This calculator does not query production data or define process specifications.
    """
    scale = {"nm": Decimal(1), "um": Decimal(1000), "mm": Decimal(1000000)}
    output = Decimal(str(value)) * scale[from_unit] / scale[to_unit]
    return f"{value} {from_unit} = {output} {to_unit}"


app = server.streamable_http_app(stateless_http=True, host="0.0.0.0", max_request_body_size=65536)
