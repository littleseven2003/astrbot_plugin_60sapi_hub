"""按任务候选顺序寻找同一站点满足全部栏目与条数的预览。

投递与聊天查询共用这一判定：只有单个站点同时满足组合的全部栏目和条数，
才算可用；不满足就换下一个候选，绝不拼接多个站点的内容。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from .site_requirements import Requirement, evaluate_sections

# 发送本身要占的时间：任何一次预览尝试都不吃掉这部分。
SEND_RESERVE_SECONDS = 0.25
# 每个还没试过的候选至少要留下的一次尝试时长。
MIN_CANDIDATE_SECONDS = 0.5


def attempt_timeout_for(
    deadline: float | None,
    now: float | None = None,
    *,
    remaining_candidates: int = 1,
) -> float | None:
    """按投递截止时间算出这一次预览尝试的时长上限。

    只给这一次尝试留下“剩余时间减去发送预留和后续候选底线”的部分，因此
    正常可用的站点能用掉几乎全部剩余时间，慢站点也吃不完后面候选的机会。
    预留最多只占剩余时间的一半：候选再多，也不能让一次本可成功的尝试被
    悲观预留提前判失败。
    """
    if deadline is None:
        return None
    if now is None:
        now = asyncio.get_running_loop().time()
    remaining = deadline - now
    if remaining <= 0:
        return 0.0
    later = max(0, remaining_candidates - 1)
    reserved = min(remaining * 0.5, SEND_RESERVE_SECONDS + later * MIN_CANDIDATE_SECONDS)
    return max(0.01, remaining - reserved)


async def satisfied_previews(
    previews: object,
    *,
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
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
            "profile_id": profile_id,
            "force_refresh": False,
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
                preview = await previews.preview_profile(payload, **kwargs)
            else:
                preview = await asyncio.wait_for(
                    previews.preview_profile(payload, **kwargs),
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
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    attempt_timeout: float | None = None,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """返回第一个满足全部要求的站点预览；全部不满足时返回 None。"""
    async for result in satisfied_previews(
        previews,
        profile_id=profile_id,
        requirements=requirements,
        site_ids=site_ids,
        owner=owner,
        catch_errors=catch_errors,
        attempt_timeout=attempt_timeout,
        deadline=deadline,
    ):
        return result
    return None


__all__ = [
    "MIN_CANDIDATE_SECONDS",
    "SEND_RESERVE_SECONDS",
    "attempt_timeout_for",
    "first_satisfying_preview",
    "satisfied_previews",
]
