# SPDX-License-Identifier: Apache-2.0
"""
Marginalia: standalone governed creative-writing application.

Serves a combined chat + governor panel at the root URL, and exposes an
OpenAI-compatible API for external clients. Governed execution and provider
ownership live in the Agent Governor daemon behind GovernedChatAdapter.
"""

__version__ = "0.1.0"
