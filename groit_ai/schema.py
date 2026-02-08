import graphene

from core.schema import Query as CoreQuery


class Query(CoreQuery, graphene.ObjectType):
    hello = graphene.String(default_value='Hello, GraphQL!')


schema = graphene.Schema(query=Query)
