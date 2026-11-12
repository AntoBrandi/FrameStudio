from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from framestudio.services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


ServicesDep = Annotated[Services, Depends(get_services)]
