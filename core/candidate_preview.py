"""按任务候选顺序寻找同一站点满足全部栏目与条数的预览。

投递与聊天查询共用这一判定：只有单个站点同时满足任务的全部栏目和条数，
才算可用；不满足就换下一个候选，绝不拼接多个站点的内容。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from .site_requirements import Requirement, evaluate_sections


def attempt_timeout_for(
    deadline: float | None,
    now: float | None = None,
    *,
    remaining_candidates: int = 1,
) -> float | None:
    """按内容准备截止时间算出这一次预览尝试的时长上限。

    内容准备阶段的剩余时间在还没试过的候选之间等份：唯一候选能用满整段，
    慢的首选站点也吃不完后面候选的机会。内容准备有独立预算，发送不再从
    这里预留；等份用尽即按“该站不满足”换下一个候选。
    """
    if deadline is None:
        return None
    if now is None:
        now = asyncio.get_running_loop().time()
    remaining = deadline - now
    if remaining <= 0:
        return 0.0
    return remaining / max(1, remaining_candidates)


async def satisfied_previews(
    previews: object,
    *,
    plan_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    force_refresh: bool = False,
    attempt_timeout: float | None = None,
    deadline: float | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """按顺序产出满足全部要求的 (site_id, 预览数据)。

    catch_errors 为真时跳过单个站点抛出的异常（聊天查询不能让一次
    预览失败中断整条链路），为假时继续向上传播（投递保留原有语义）。
    attempt_timeout 限制单个候选站点的尝试时长：超时视作该站不满足并继续
    下一个候选，不必等它把所有时间用完。
    给出 deadline 时按剩余时间和“还有几个候选没试”逐次计算上限（投递用），
    否则用固定值。
    """
    pending = list(site_ids)
    for index, site_id in enumerate(pending):
        payload: dict[str, Any] = {
            "task_id": plan_id,
            # 后台“预览内容”可以要求跳过缓存；聊天与投递始终用当前缓存策略。
            "force_refresh": force_refresh,
            "site_id": site_id,
        }
        # 站点同时放进载荷与关键字参数：预览接口两处都接受，调用方无需区分。
        kwargs: dict[str, Any] = {"site_id": site_id}
        if owner is not None:
            kwargs["owner"] = owner
        timeout = (
            attempt_timeout
            if deadline is None
            else attempt_timeout_for(
                deadline, remaining_candidates=len(pending) - index
            )
        )
        try:
            if timeout is None:
                preview = await previews.preview_site(payload, **kwargs)
            else:
                preview = await asyncio.wait_for(
                    previews.preview_site(payload, **kwargs),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            # 超时与“该站不满足”同等对待：换下一个候选，而不是直接失败。
            continue
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
    plan_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    force_refresh: bool = False,
    attempt_timeout: float | None = None,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """返回第一个满足全部要求的站点预览；全部不满足时返回 None。"""
    async for result in satisfied_previews(
        previews,
        plan_id=plan_id,
        requirements=requirements,
        site_ids=site_ids,
        owner=owner,
        catch_errors=catch_errors,
        force_refresh=force_refresh,
        attempt_timeout=attempt_timeout,
        deadline=deadline,
    ):
        return result
    return None


async def assembled_preview(
    previews: object,
    *,
    plan_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    force_refresh: bool = False,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """优先使用 PlanPreview 的单站优先 + 按栏拼合；旧预览桩回退单站语义。"""
    assembled = getattr(previews, "preview_assembled", None)
    if callable(assembled):
        try:
            response = await assembled(
                {"task_id": plan_id, "force_refresh": force_refresh},
                site_ids=list(site_ids),
                owner=owner,
                deadline=deadline,
            )
        except Exception:
            if catch_errors:
                return None
            raise
        if getattr(response, "status_code", None) != 200:
            return None
        body = getattr(response, "body", None)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None
        return str(data.get("site_id") or ""), data
    return await first_satisfying_preview(
        previews,
        plan_id=plan_id,
        requirements=requirements,
        site_ids=site_ids,
        owner=owner,
        catch_errors=catch_errors,
        force_refresh=force_refresh,
        deadline=deadline,
    )


__all__ = [
    "assembled_preview",
    "attempt_timeout_for",
    "first_satisfying_preview",
    "satisfied_previews",
]
