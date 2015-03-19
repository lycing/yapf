# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Computation of split penalties before/between tokens."""

from lib2to3 import pytree

from yapf.yapflib import py3compat
from yapf.yapflib import pytree_utils
from yapf.yapflib import pytree_visitor

UNBREAKABLE = 1000 * 1000
STRONGLY_CONNECTED = 1000
ARITHMETIC_EXPRESSION = 42

# TODO(morbo): Document the annotations in a centralized place. E.g., the
# README file.


def ComputeSplitPenalties(tree):
  """Compute split penalties on tokens in the given parse tree.

  Arguments:
    tree: the top-level pytree node to annotate with penalties.
  """
  _TreePenaltyAssigner().Visit(tree)


class _TreePenaltyAssigner(pytree_visitor.PyTreeVisitor):
  """Assigns split penalties to tokens, based on parse tree structure.

  Split penalties are attached as annotations to tokens.
  """

  def Visit_classdef(self, node):  # pylint: disable=invalid-name
    # classdef ::= 'class' NAME ['(' [arglist] ')'] ':' suite
    #
    # NAME
    self._SetUnbreakable(node.children[1])
    if len(node.children) > 4:
      # opening '('
      self._SetUnbreakable(node.children[2])
    # ':'
    self._SetUnbreakable(node.children[-2])
    self.DefaultNodeVisit(node)

  def Visit_funcdef(self, node):  # pylint: disable=invalid-name
    # funcdef ::= 'def' NAME parameters ['->' test] ':' suite
    #
    # Can't break before the function name and before the colon. The parameters
    # are handled by child iteration.
    colon_idx = 1
    while pytree_utils.NodeName(node.children[colon_idx]) == 'simple_stmt':
      colon_idx += 1
    self._SetUnbreakable(node.children[colon_idx])
    while colon_idx < len(node.children):
      if (isinstance(node.children[colon_idx], pytree.Leaf) and
          node.children[colon_idx].value == ':'):
        break
      colon_idx += 1
    self._SetUnbreakable(node.children[colon_idx])
    self.DefaultNodeVisit(node)

  def Visit_lambdef(self, node):  # pylint: disable=invalid-name
    # lambdef ::= 'lambda' [varargslist] ':' test
    # Loop over the lambda up to and including the colon.
    lambda_has_arglist = pytree_utils.NodeName(node.children[1]) != 'COLON'
    self._SetUnbreakableOnChildren(node,
                                   num_children=3 if lambda_has_arglist else 2)

  def Visit_parameters(self, node):  # pylint: disable=invalid-name
    # parameters ::= '(' [typedargslist] ')'
    self.DefaultNodeVisit(node)

    # Can't break before the opening paren of a parameter list.
    self._SetUnbreakable(node.children[0])
    if len(node.children) == 2:
      # Don't split an empty argument list if at all possible.
      self._SetStronglyConnected(node.children[1])

  def Visit_dotted_name(self, node):  # pylint: disable=invalid-name
    # dotted_name ::= NAME ('.' NAME)*
    self._SetUnbreakableOnChildren(node, num_children=len(node.children))

  def Visit_dictsetmaker(self, node):  # pylint: disable=invalid-name
    # dictsetmaker ::= ( (test ':' test
    #                      (comp_for | (',' test ':' test)* [','])) |
    #                    (test (comp_for | (',' test)* [','])) )
    prev_child = None
    for child in node.children:
      self.Visit(child)
      if pytree_utils.NodeName(child) == 'COLON':
        # This is a key to a dictionary. We don't want to split the key if at
        # all possible.
        self._SetStronglyConnected(prev_child, child)
      prev_child = child

  def Visit_comparison(self, node):  # pylint: disable=invalid-name
    # comparison ::= expr (comp_op expr)*
    self.DefaultNodeVisit(node)
    self._SetArithmeticExpression(node)

  def Visit_arith_expr(self, node):  # pylint: disable=invalid-name
    # arith_expr ::= term (('+'|'-') term)*
    self.DefaultNodeVisit(node)
    self._SetArithmeticExpression(node)

  def Visit_term(self, node):  # pylint: disable=invalid-name
    # term ::= factor (('*'|'/'|'%'|'//') factor)*
    self.DefaultNodeVisit(node)
    self._SetArithmeticExpression(node)

  def Visit_trailer(self, node):  # pylint: disable=invalid-name
    # trailer ::= '(' [arglist] ')' | '[' subscriptlist ']' | '.' NAME
    self.DefaultNodeVisit(node)
    if node.children[0].value == '.':
      self._SetStronglyConnected(node.children[0], node.children[-1])
    elif node.children[0].value == '[':
      self._SetStronglyConnected(node.children[-1])

  def Visit_power(self, node):  # pylint: disable=invalid-name,missing-docstring
    # power: atom trailer* ['**' factor]
    self.DefaultNodeVisit(node)

    # See if this node is surrounded by parentheses. If it is, then we can
    # relax some of the formatting restrictions.
    surrounded_by_parens = (
        node.parent and pytree_utils.NodeName(node.parent) == 'atom' and
        isinstance(node.parent.children[0], pytree.Leaf) and
        node.parent.children[0].value == '(' and
        isinstance(node.parent.children[-1], pytree.Leaf) and
        node.parent.children[-1].value == ')')

    # When atom is followed by a trailer, we can not break between them.
    # E.g. arr[idx] - no break allowed between 'arr' and '['.
    if (len(node.children) > 1 and
        pytree_utils.NodeName(node.children[1]) == 'trailer'):
      # children[1] itself is a whole trailer: we don't want to
      # mark all of it as unbreakable, only its first token: (, [ or .
      self._SetUnbreakable(node.children[1].children[0])

      # A special case when there are more trailers in the sequence. Given:
      #   atom tr1 tr2
      # The last token of tr1 and the first token of tr2 comprise an unbreakable
      # region. For example: foo.bar.baz(1)
      # We can't put breaks between either of the '.' or the '(' and the names
      # *preceding* them.
      prev_trailer_idx = 1
      while prev_trailer_idx < len(node.children) - 1:
        cur_trailer_idx = prev_trailer_idx + 1
        cur_trailer = node.children[cur_trailer_idx]
        if pytree_utils.NodeName(cur_trailer) == 'trailer':
          # Now we know we have two trailers one after the other
          prev_trailer = node.children[prev_trailer_idx]
          if prev_trailer.children[-1].value != ')':
            # Set the previous node unbreakable if it's not a function call:
            #   atom tr1() tr2
            # It may be necessary (though undesirable) to split up a previous
            # function call's parentheses to the next line.
            self._SetUnbreakable(prev_trailer.children[-1])
          if not surrounded_by_parens:
            # If this is surrounded by parentheses, we can allow the '.' to be
            # on the next line. This is for "builder" type calling chains.
            self._SetUnbreakable(cur_trailer.children[0])
          prev_trailer_idx = cur_trailer_idx
        else:
          break

    # We don't want to split before the last ')' of a function call. This also
    # takes care of the special case of:
    #   atom tr1 tr2 ... trn
    # where the 'tr#' are trailers that may end in a ')'.
    for trailer in node.children[1:]:
      if pytree_utils.NodeName(trailer) != 'trailer':
        break
      if trailer.children[0].value == '(' and len(trailer.children) > 2:
        # If the trailer's children are '()', then don't set the ')' as
        # unbreakable. It's sometimes necessary, though undesirable, to split
        # the two.
        self._SetUnbreakable(trailer.children[-1])

  def Visit_subscript(self, node):  # pylint: disable=invalid-name
    # subscript ::= test | [test] ':' [test] [sliceop]
    self._SetStronglyConnected(*node.children)
    self.DefaultNodeVisit(node)

  def Visit_comp_for(self, node):  # pylint: disable=invalid-name
    # comp_for ::= 'for' exprlist 'in' testlist_safe [comp_iter]
    self._SetStronglyConnected(*node.children[1:])
    self.DefaultNodeVisit(node)

  def Visit_comp_if(self, node):  # pylint: disable=invalid-name
    # comp_if ::= 'if' old_test [comp_iter]
    pytree_utils.SetNodeAnnotation(node.children[0],
                                   pytree_utils.Annotation.SPLIT_PENALTY, None)
    self._SetStronglyConnected(*node.children[1:])
    self.DefaultNodeVisit(node)

  ############################################################################
  # Helper methods that set the annotations.

  def _SetUnbreakable(self, node):
    """Set an UNBREAKABLE penalty annotation for the given node."""
    self._RecAnnotate(node, pytree_utils.Annotation.SPLIT_PENALTY, UNBREAKABLE)

  def _SetStronglyConnected(self, *nodes):
    """Set a STRONGLY_CONNECTED penalty annotation for the given nodes."""
    for node in nodes:
      self._RecAnnotate(node, pytree_utils.Annotation.SPLIT_PENALTY,
                        STRONGLY_CONNECTED)

  def _SetUnbreakableOnChildren(self, node, num_children):
    """Set an UNBREAKABLE penalty annotation on children of node."""
    for child in node.children:
      self.Visit(child)
    for i in py3compat.range(1, num_children):
      self._SetUnbreakable(node.children[i])

  def _SetArithmeticExpression(self, node):
    """Set an ARITHMETIC_EXPRESSION penalty annotation children nodes."""

    def FirstChildNode(node):
      if isinstance(node, pytree.Leaf):
        return node
      return FirstChildNode(node.children[0])

    def RecArithmeticExpression(node, first_child_leaf):
      if node is first_child_leaf:
        return

      if isinstance(node, pytree.Leaf):
        if pytree_utils.GetNodeAnnotation(
            node,
            pytree_utils.Annotation.SPLIT_PENALTY) < ARITHMETIC_EXPRESSION:
          pytree_utils.SetNodeAnnotation(
              node,
              pytree_utils.Annotation.SPLIT_PENALTY, ARITHMETIC_EXPRESSION)
      else:
        for child in node.children:
          RecArithmeticExpression(child, first_child_leaf)

    RecArithmeticExpression(node, FirstChildNode(node))

  def _RecAnnotate(self, tree, annotate_name, annotate_value):
    """Recursively set the given annotation on all leafs of the subtree.

    Takes care to only increase the penalty. If the node already has a higher
    or equal penalty associated with it, this is a no-op.

    Args:
      tree: subtree to annotate
      annotate_name: name of the annotation to set
      annotate_value: value of the annotation to set
    """
    for child in tree.children:
      self._RecAnnotate(child, annotate_name, annotate_value)
    if isinstance(tree, pytree.Leaf):
      cur_annotate = pytree_utils.GetNodeAnnotation(tree, annotate_name)
      if cur_annotate is None or cur_annotate < annotate_value:
        pytree_utils.SetNodeAnnotation(tree, annotate_name, annotate_value)