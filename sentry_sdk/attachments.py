import os
import mimetypes

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.envelope import Item, PayloadRef

if TYPE_CHECKING:
    from typing import Optional, Union, Callable


class Attachment:
    def __init__(
        self,
        bytes: Union[None, bytes, Callable[[], bytes]] = None,
        filename: Optional[str] = None,
        path: Optional[str] = None,
        content_type: Optional[str] = None,
        add_to_transactions: bool = False,
    ) -> None:
        if bytes is None and path is None:
            raise TypeError("path or raw bytes required for attachment")
        if filename is None and path is not None:
            filename = os.path.basename(path)
        if filename is None:
            raise TypeError("filename is required for attachment")
        if content_type is None:
            content_type = mimetypes.guess_type(filename)[0]
        self.bytes = bytes
        self.filename = filename
        self.path = path
        self.content_type = content_type
        self.add_to_transactions = add_to_transactions

    def to_envelope_item(self) -> Item:
        """Returns an envelope item for this attachment."""
        payload: Union[None, PayloadRef, bytes] = None
        if self.bytes is not None:
            if callable(self.bytes):
                payload = self.bytes()
            else:
                payload = self.bytes
        else:
            payload = PayloadRef(path=self.path)
        return Item(
            payload=payload,
            type="attachment",
            content_type=self.content_type,
            filename=self.filename,
        )

    def __repr__(self) -> str:
        return "<Attachment %r>" % (self.filename,)
