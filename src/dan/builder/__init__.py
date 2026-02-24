"""dan.builder — fluent Python DSL for defining DAN workflows.

Usage::

    from dan.builder import workflow

    wf = workflow("my_workflow")
    a = wf.llm("gen", model="claude-opus-4", prompt="Generate: {topic}")
    b = wf.llm("refine", prompt=f"Refine: {a}")
    a >> b
    graph = wf.build()
"""

from dan.builder.builder import WorkflowBuilder, workflow
from dan.builder.compiler import BuildError
from dan.builder.decompiler import decompile
from dan.builder.importer import derive_ports, namespace_graph
from dan.builder.refs import NodeRef, PortRef

__all__ = [
    "WorkflowBuilder",
    "workflow",
    "BuildError",
    "decompile",
    "NodeRef",
    "PortRef",
    "namespace_graph",
    "derive_ports",
]
