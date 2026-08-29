"""Marca la raíz del proyecto para pytest.

Su sola presencia hace que pytest inserte esta carpeta en ``sys.path``, de modo
que ``import telmeow_teleop`` funcione sin instalar el paquete ni declarar un
pyproject.toml.
"""
