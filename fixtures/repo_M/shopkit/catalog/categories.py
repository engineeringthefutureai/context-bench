"""Category tree for storefront navigation."""
from dataclasses import dataclass, field

from shopkit.utils.text import slug_for_title


@dataclass
class Category:
    id: str
    name: str
    parent_id: str | None = None
    children: list["Category"] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slug_for_title(self.name)


class CategoryTree:
    def __init__(self, categories: list[Category]):
        self._by_id = {c.id: c for c in categories}
        self.roots: list[Category] = []
        for c in categories:
            parent = self._by_id.get(c.parent_id) if c.parent_id else None
            (parent.children if parent else self.roots).append(c)

    def get(self, category_id: str) -> Category:
        return self._by_id[category_id]

    def path(self, category_id: str) -> list[Category]:
        """Root-to-leaf chain ending at category_id; used for breadcrumbs."""
        chain = []
        current: Category | None = self._by_id[category_id]
        while current is not None:
            chain.append(current)
            current = self._by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(chain))

    def breadcrumb(self, category_id: str) -> str:
        return " / ".join(c.name for c in self.path(category_id))

    def url(self, category_id: str) -> str:
        return "/c/" + "/".join(c.slug for c in self.path(category_id))

    def descendants(self, category_id: str) -> list[Category]:
        out, stack = [], list(self._by_id[category_id].children)
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(node.children)
        return out
