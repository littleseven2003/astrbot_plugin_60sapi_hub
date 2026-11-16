"""按任务候选顺序寻找同一站点满足全部栏目与条数的预览。

投递与聊天查询共用这一判定：只有单个站点同时满足组合的全部栏目和条数，
才算可用；不满足就换下一个候选，绝不拼接多个站点的内容。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

from .site_requirements import Requirement, evaluate_sections


async def satisfied_previews(
    previews: object,
    *,
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """按顺序产出满足全部要求的 (site_id, 预览数据)。

    catch_errors 为真时跳过单个站点抛出的异常（聊天查询不能让一次
    预览失败中断整条链路），为假时继续向上传播（投递保留原有语义）。
    """
    for site_id in site_ids:
        payload: dict[str, Any] = {
            "profile_id": profile_id,
            "force_refresh": False,
            "site_id": site_id,
        }
        # 站点同时放进载荷与关键字参数：预览接口两处都接受，调用方无需区分。
        kwargs: dict[str, Any] = {"site_id": site_id}
        if owner is not None:
            kwargs["owner"] = owner
        try:
            preview = await previews.preview_profile(payload, **kwargs)
        except Exception:
            if not catch_errors:
                raise
            continue
        if getattr(preview, "status_code", None) != 200:
            continue
        body = getattr(preview, "body", None)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            continue
        sections = data.get("sections")
        if isinstance(sections, list):
            satisfied, _ = evaluate_sections(sections, requirements)
            if not satisfied:
                continue
        yield site_id, data


async def first_satisfying_preview(
    previews: object,
    *,
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
) -> tuple[str, dict[str, Any]] | None:
    """返回第一个满足全部要求的站点预览；全部不满足时返回 None。"""
    async for result in satisfied_previews(
        previews,
        profile_id=profile_id,
        requirements=requirements,
        site_ids=site_ids,
        owner=owner,
        catch_errors=catch_errors,
    ):
        return result
    return None


__all__ = ["first_satisfying_preview", "satisfied_previews"]
