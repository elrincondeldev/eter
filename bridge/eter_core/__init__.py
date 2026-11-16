"""eter core: all bridge logic lives here. The other packages are thin facades."""

from eter_core.eter import Eter, open_eter
from eter_core.node import Node, NodeError

__all__ = ["Eter", "Node", "NodeError", "open_eter"]
